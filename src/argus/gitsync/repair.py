# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair step: find a pull request this machine published whose checks finished red, and prepare one repair run for it."""

import contextlib
import json
import time
from pathlib import Path
from typing import Any, cast

from argus.conventions import runs_dir, setting
from argus.gitsync.gh_gate import gh_pull_request_checks
from argus.gitsync.gh_repair import gh_failed_run_log, gh_pull_request_head
from argus.gitsync.git import _is_private_path, current_branch, dirty_paths, git
from argus.gitsync.group_gate import checks_verdict
from argus.gitsync.merge import sync_one
from argus.gitsync.receipts import _phase_run_entries, phase_receipt, pull_request_ref
from argus.gitsync.repair_hold import waiting_hold
from argus.gitsync.repair_scan import alert_report, checks_without_run, run_ids
from argus.gitsync.repair_task import (
    alerts_section,
    log_section,
    task_header,
    verify_failure,
    verify_report,
)

_SETTLED = ("pass", "skipping", "fail", "cancel")


def _record(root: Path, number: int) -> tuple[Path, dict[str, Any]]:
    """The repair record of one pull request: attempts made, and how long it has waited."""
    path = runs_dir(root) / setting("driver.state") / "repair" / f"pr-{number}.json"
    record: dict[str, Any] = {
        "attempts": 0,
        "rounds": 0,
        "base": "",
        "notified": False,
        "head": "",
        "waiting_since": None,
        "nonce": "",
    }
    with contextlib.suppress(OSError, ValueError):
        record.update(json.loads(path.read_text(encoding="utf-8")))
    # A record written before rounds existed counted every round as an attempt.
    record["rounds"] = max(int(record["rounds"] or 0), int(record["attempts"]))
    return path, record


def save_record(path: Path, record: dict[str, Any]) -> None:
    """Write a repair record as sorted JSON, so a read diff stays a diff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")


def _checkout(root: Path, branch: str) -> str | None:
    """Put the checkout on the pull request's branch, current with its remote.

    Returns why it could not, or ``None``. A checkout with uncommitted work is
    never switched, and a branch name that git could read as an option is
    refused, because the name comes from a receipt.
    """
    if branch.startswith("-"):
        return f"refusing the branch name {branch!r}"
    if current_branch(root) != branch:
        if [p for p in dirty_paths(root) if not _is_private_path(root, p)]:
            return f"the checkout holds uncommitted work; not switching to {branch!r}"
        git(root, "fetch", "origin", branch)
        rc, _, _ = git(root, "checkout", branch)
        if rc != 0:
            rc, _, err = git(
                root, "checkout", "-b", branch, "--track", f"origin/{branch}"
            )
            if rc != 0:
                return f"cannot check out {branch!r}: {err}"
    synced = sync_one(root, "start")
    return None if synced.get("ok") else f"cannot bring {branch!r} up to date"


def _failure_logs(ref: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """The failed steps' log of every failing run, joined, or ``None`` if unreadable."""
    logs: list[str] = []
    for run in run_ids(rows):
        text = gh_failed_run_log(run, ref["owner"], ref["repo"], ref["host"]).value
        if text:
            logs.append(text)
    return "\n".join(logs) if logs else None


def _blocked_reason(names: list[str], alerts_why: str) -> str:
    """Why no repair could be prepared, naming a failed read before a missing one."""
    if alerts_why:
        return f"the code-scanning alerts could not be read ({alerts_why})"
    if names:
        return (
            f"no readable output for {', '.join(names)}: "
            "no workflow run, and no open code-scanning alert"
        )
    return "the failed run's log could not be read"


def _spend_previous(
    path: Path, number: int, record: dict[str, Any], expired: bool = False
) -> dict[str, Any]:
    """Count the round that already ran, unless its own scope failed.

    A round's own check decides whether it spends an attempt, so the budget stays
    open for the failure that round was meant to clear. The answer names a
    regression with the task section for it, or says that the check of that round
    is still running, so a check that outlives the CI result is never read as a
    pass. ``expired`` comes from a caller whose wait for that check ran out: the
    missing answer then reads as no check, which counts the round and leaves the
    pull request able to end.
    """
    if record["attempts"] >= record["rounds"]:
        return {}
    folder = path.parent / f"{number}-{record['rounds']}"
    status, rc = verify_report(folder, record["nonce"])
    if status == "running" and not expired:
        return {"running": True}
    if status == "fail":
        return {"regression": verify_failure(folder, rc)}
    record["attempts"] += 1
    save_record(path, record)
    return {}


def _exhausted_summary(
    url: str, limit: int, record: dict[str, Any], regression: tuple[str, str] | None
) -> str:
    """Why a pull request was given up, naming a regression in the last round."""
    summary = (
        f"repair EXHAUSTED: {record['attempts']} of {limit} attempt(s) and "
        f"{record['rounds']} round(s) on {url} did not make the checks pass"
    )
    if regression:
        summary += (
            f"; the last round was a self-introduced regression ({regression[0]})"
        )
    return summary


def _consider(
    root: Path, ref: dict[str, Any], branch: str, now: float
) -> dict[str, Any] | None:
    """One pull request's turn: hold the line, repair, give up, or nothing (``None``)."""
    path, record = _record(root, ref["number"])
    url = f"{ref['owner']}/{ref['repo']}#{ref['number']}"
    head = gh_pull_request_head(ref["number"], ref["owner"], ref["repo"], ref["host"])
    if head.value is None:
        # A failed head read must not read as "no change": hold and name it.
        return waiting_hold(
            root,
            path,
            record,
            None,
            now,
            f"could not read the pull request {url} ({head.why})",
        )
    found = head.value
    if found[0] != "OPEN":
        return None
    state_head = found[1]
    checks = gh_pull_request_checks(
        ref["number"], ref["owner"], ref["repo"], ref["host"]
    )
    rows = checks.value
    verdict, names = checks_verdict(rows)
    if verdict == "passing":
        path.unlink(missing_ok=True)
        return None
    if not rows or any(row.get("bucket") not in _SETTLED for row in rows):
        # A failed read names its reason as an exit code or a timeout, never gh's stderr text.
        detail = (
            f"checks on {url} are not finished"
            if rows
            else f"could not read the checks on {url} ({checks.why})"
        )
        return waiting_hold(root, path, record, state_head, now, detail)
    settled = _spend_previous(path, ref["number"], record)
    if settled.get("running"):
        hold = waiting_hold(
            root,
            path,
            record,
            state_head,
            now,
            f"the changed-scope check of round {record['rounds']} has not finished",
        )
        if hold is not None:
            return hold
        # The wait ran out with the check still running, so the round is read as
        # having no answer: counting it keeps a check that never ends from
        # holding the pull request open forever.
        settled = _spend_previous(path, ref["number"], record, expired=True)
    regression = cast("tuple[str, str] | None", settled.get("regression"))
    limit = setting("repair.attempts")
    # A round is counted only when the next turn reads its own scope as passing,
    # so a self-introduced regression does not spend the budget meant for the
    # original failure. The round cap keeps a repair that keeps regressing from
    # running forever.
    if record["attempts"] >= limit or record["rounds"] >= 2 * limit:
        first = not record["notified"]
        record["notified"] = True
        save_record(path, record)
        return {
            "state": "EXHAUSTED",
            "notify": first,
            "summary": _exhausted_summary(url, limit, record, regression),
        }
    problem = _checkout(root, branch)
    if problem:
        return {"state": "BLOCKED", "summary": f"repair BLOCKED on {url}: {problem}"}
    log = _failure_logs(ref, rows or [])
    without_run = checks_without_run(rows or [])
    alerts, alerts_why = alert_report(ref) if without_run else (None, "")
    if log is None and alerts is None:
        reason = _blocked_reason(without_run, alerts_why)
        return {"state": "BLOCKED", "summary": f"repair BLOCKED on {url}: {reason}"}
    round_number = record["rounds"] + 1
    nonce = f"{round_number}-{int(now)}"
    folder = path.parent / f"{ref['number']}-{round_number}"
    folder.mkdir(parents=True, exist_ok=True)
    task = folder / "task.md"
    task.write_text(
        task_header(url, branch, record["attempts"] + 1, limit, names, round_number)
        + (regression[1] if regression else "")
        + (log_section(folder, log) if log is not None else "")
        + (alerts_section(folder, alerts) if alerts is not None else ""),
        encoding="utf-8",
    )
    record.update(rounds=round_number, head=state_head, waiting_since=None, nonce=nonce)
    record["base"] = record["base"] or state_head
    save_record(path, record)
    return {
        "state": "REPAIR",
        "id": f"{ref['number']}-{round_number}",
        "task": str(task),
        "branch": branch,
        "base": record["base"],
        "nonce": nonce,
        "attempt": round_number,
        "attempts": limit,
        "summary": (
            f"repair REPAIR: attempt {record['attempts'] + 1} of {limit} "
            f"(round {round_number}) on {url} for {', '.join(names)}"
            + (f"; self-introduced regression: {regression[0]}" if regression else "")
        ),
    }


def mode_repair(root: str | Path) -> dict[str, Any]:
    """Find the pull request that needs a repair run now, and prepare it.

    Only a pull request this machine published is looked at, newest first, one
    per pull request and no more than ``driver.merge-check-max`` of them. The
    answer's ``state`` is ``REPAIR`` (a task is ready), ``HOLD`` (the checks are
    still running or a read failed, so nothing new should start), ``EXHAUSTED``,
    ``BLOCKED`` or ``NONE``.
    """
    root = Path(root).resolve()
    mine, cap, now = (
        setting("machine.id"),
        setting("driver.merge-check-max"),
        time.time(),
    )
    seen: set[int] = set()
    for number, _entry in reversed(list(_phase_run_entries(root))):
        receipt = phase_receipt(root, number)
        if not isinstance(receipt, dict) or receipt.get("state") != "awaiting_review":
            continue
        ref = pull_request_ref(receipt.get("pull_request"))
        branch = receipt.get("branch")
        reservation = cast("dict[str, Any]", receipt.get("reservation") or {})
        owner = reservation.get("machine_id")
        if owner != mine or not ref or not branch or ref["number"] in seen:
            continue
        seen.add(ref["number"])
        if len(seen) > cap:
            break
        found = _consider(root, ref, str(branch), now)
        if found is not None:
            return {"ok": True, "mode": "repair", "phase": f"{number:06d}", **found}
    return {"ok": True, "mode": "repair", "state": "NONE", "summary": "repair NONE"}


__all__ = ["mode_repair"]
