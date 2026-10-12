# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group's gate: merge the fix pull requests waiting on its branch, then ready its pull request once the checks are green."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argus.conventions import runs_dir, setting
from argus.gitsync.gh_gate import (
    gh_merge_pull_request,
    gh_open_pull_requests,
    gh_pull_request_checks,
)
from argus.gitsync.group_review import _group_pending, mark_group_pull_request_ready
from argus.gitsync.receipts import phase_receipt, pull_request_ref

_PASSING = ("pass", "skipping")
_FAILING = ("fail", "cancel")


def checks_verdict(rows: list[dict[str, Any]] | None) -> tuple[str, list[str]]:
    """``(verdict, names)`` for a pull request's checks.

    The verdict is ``passing``, ``failing``, ``pending`` or ``unknown``, and
    ``names`` are the checks that hold it back. ``unknown`` is no rows at all,
    which is also what GitHub reports in the seconds after a push, before the
    workflows have registered: it is never read as passing. A bucket gh adds
    later reads as pending, so a new kind of check holds the gate and does not
    open it.
    """
    if not rows:
        return "unknown", []
    failing = [str(r.get("name")) for r in rows if r.get("bucket") in _FAILING]
    if failing:
        return "failing", failing
    running = [str(r.get("name")) for r in rows if r.get("bucket") not in _PASSING]
    if running:
        return "pending", running
    return "passing", []


def _checks_reason(verdict: str, names: list[str]) -> str:
    detail = f": {', '.join(names)}" if names else ""
    return f"checks {verdict}{detail}"


def _fix_pull_request_hold(pr: dict[str, Any], ref: dict[str, Any]) -> str | None:
    """Why a pull request on the group branch is not merged now, or ``None``.

    A pull request from a fork is never merged: a public repository takes them
    from strangers, and the branch they target is the one a reviewer reads as
    the group's own work. The checks are read last because they cost a call.
    """
    if pr.get("isDraft"):
        return "draft"
    if pr.get("isCrossRepository"):
        return "from a fork"
    if pr.get("mergeable") != "MERGEABLE":
        return f"mergeable is {str(pr.get('mergeable')).lower()}"
    if pr.get("reviewDecision") == "CHANGES_REQUESTED":
        return "changes requested"
    rows, _why = gh_pull_request_checks(
        pr["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
    )
    verdict, names = checks_verdict(rows)
    return None if verdict == "passing" else _checks_reason(verdict, names)


def merge_fix_pull_requests(branch: str, ref: dict[str, Any]) -> dict[str, Any]:
    """Merge every pull request waiting on the group branch that is safe to merge.

    A later member of a group files the bugs an earlier member left, and a
    person or an agent fixes them in a pull request whose base is the group
    branch. Merged here, the fix reaches the branch before the next member
    starts, so that member does not file the same bug again.

    Returns ``merged`` (numbers), ``held`` (number and reason for each pull
    request left open), and ``unknown`` (why GitHub could not be asked, or
    empty). A pull request is merged only when it is ready, from this
    repository, mergeable, not blocked by a review, and green; and only at the
    head those checks were read for.
    """
    owner, repo, host = ref.get("owner"), ref.get("repo"), ref.get("host")
    result: dict[str, Any] = {"merged": [], "held": [], "unknown": ""}
    prs, why = gh_open_pull_requests(branch, owner, repo, host)
    if prs is None:
        result["unknown"] = why
        return result
    for pr in prs:
        number = pr["number"]
        reason = _fix_pull_request_hold(pr, ref)
        if reason is None:
            merged, detail = gh_merge_pull_request(
                number, str(pr.get("headRefOid")), owner, repo, host
            )
            if merged:
                result["merged"].append(number)
                continue
            reason = detail
        result["held"].append({"number": number, "reason": reason})
    return result


def _ready_marker(root: str | Path, members: list[str]) -> Path:
    """The file recording that the group's pull request was made ready.

    It lives in the driver's own folder, which is not tracked, and is named for
    the group's highest member so every member finds the same one. Ready happens
    once: a person who sets the pull request back to draft is not undone on the
    next tick.
    """
    anchor = max(int(m) for m in members)
    return runs_dir(root) / setting("driver.state") / f"group-ready-{anchor:06d}.json"


def _gate_reasons(
    fixes: dict[str, Any], rows: list[dict[str, Any]] | None
) -> list[str]:
    reasons: list[str] = []
    if fixes["unknown"]:
        reasons.append(
            f"pull requests on the group branch unreadable: {fixes['unknown']}"
        )
    held = [f"#{h['number']} ({h['reason']})" for h in fixes["held"]]
    if held:
        reasons.append(f"open pull requests on the group branch: {', '.join(held)}")
    if fixes["merged"]:
        reasons.append("fix merged just now; the checks of the new head are not in yet")
    elif not reasons:
        verdict, names = checks_verdict(rows)
        if verdict != "passing":
            reasons.append(_checks_reason(verdict, names))
    return reasons


def release_group_pull_request(
    root: str | Path,
    phase: int,
    members: list[str],
    pull_request: str | None,
    branch: str | None,
) -> dict[str, Any] | None:
    """Merge the group's fix pull requests, then ready its pull request when green.

    The one place a group's shared pull request leaves draft. Every publish and
    every driver tick that finds the group waiting for review runs it, so the
    answer follows the checks as they finish: a pull request held for red or
    running checks is readied on the first tick after they pass, and a fix that
    lands re-opens the question, because it moves the head the checks judge.

    Returns ``state`` (``WAITING`` while a member is still to publish, ``HELD``,
    ``READY``), the ``reasons`` it is held, the ``fix_pull_requests`` result and,
    once the transition ran, ``ready``. ``None`` when there is no branch or no
    pull request to act on. Never raises: GitHub being unreachable is a hold.
    """
    ref = pull_request_ref(pull_request)
    if not ref or not branch or not members:
        return None
    fixes = merge_fix_pull_requests(branch, ref)
    out: dict[str, Any] = {"fix_pull_requests": fixes, "reasons": []}
    pending = _group_pending(root, members, phase)
    if pending:
        out.update(state="WAITING", pending=pending)
        return out
    marker = _ready_marker(root, members)
    if marker.exists():
        return dict(out, state="READY", reasons=[])
    rows, _why = gh_pull_request_checks(
        ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
    )
    out["reasons"] = _gate_reasons(fixes, rows)
    if out["reasons"]:
        return dict(out, state="HELD")
    ready = mark_group_pull_request_ready(phase, [], pull_request)
    out["ready"] = ready
    if not ready or not ready["ok"]:
        out["reasons"] = [str(ready["detail"] if ready else "no pull request to ready")]
        return dict(out, state="HELD")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat()
        marker.write_text(json.dumps({"ready_at": stamp}) + "\n")
    except OSError as exc:
        # Ready already happened. A marker that cannot be written costs one more
        # ready call on the next tick, and must not turn the transition into a
        # failure.
        out["marker_error"] = str(exc)
    return dict(out, state="READY")


def mode_gate(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Run the group gate for the group a published phase belongs to.

    The driver calls this on a tick that finds a phase waiting for review: the
    gate is not a step of any one phase, because the checks it waits on finish
    after every phase has. A phase outside a group has nothing to gate.
    """
    root = Path(root).resolve()
    receipt = phase_receipt(root, int(phase))
    group = receipt.get("group") if isinstance(receipt, dict) else None
    out: dict[str, Any] | None = None
    if isinstance(receipt, dict) and group:
        out = release_group_pull_request(
            root,
            int(phase),
            [str(m) for m in group],
            receipt.get("pull_request"),
            receipt.get("branch"),
        )
    if out is None:
        out = {"state": "NO-GROUP", "reasons": []}
    merged = out.get("fix_pull_requests", {}).get("merged", [])
    reasons = out["reasons"]
    summary = f"group gate {out['state']}"
    summary += f" ({'; '.join(reasons)})" if reasons else ""
    summary += f"; merged fix pull request(s) {merged}" if merged else ""
    return {
        "ok": True,
        "mode": "gate",
        "phase": f"{int(phase):06d}",
        **out,
        "summary": summary,
    }


__all__ = [
    "checks_verdict",
    "merge_fix_pull_requests",
    "mode_gate",
    "release_group_pull_request",
]
