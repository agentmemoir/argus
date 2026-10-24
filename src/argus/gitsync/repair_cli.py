# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Command entry for one tick's CI repair step on one pull request.

The driver runs it the way it runs ``board/remedy.py``: ``"$PYTHON" "$ARGUS/
gitsync/repair_cli.py" ...``. It reads the checks and the merge state once,
decides with ``repair_round.decide``, and prints one JSON object: ``action``
(``none``, ``wait``, ``repair``, ``escalate``), plus ``task`` on repair and
``message`` on escalate, and ``waiting`` while the pull request still needs the
driver. A repair decision writes the task file but counts
nothing. The driver runs the command again with ``--launched`` once the repair
run has started, and only that call counts the round, so a launch that fails can
never use up a round or lead to a false escalation.

A checks read or a merge-state read that fails exits 3 and changes nothing: an
unread pull request is not a green one.

``issue_pull_requests`` lists the open pull requests of ``issues`` work for the
driver's tick. It reads only branches that Argus recorded in an issue run
folder, so a pull request on any other branch is never repaired.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.board.core import CoordinationError
from argus.gitsync import pending_limit, repair_round
from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.gh_read import gh_pull_requests_for_branch
from argus.gitsync.git import GH_TIMEOUT_S, ConfigError, gh_binary
from argus.gitsync.issue_paths import issue_branch, issue_scope
from argus.gitsync.pr_head import gh_pull_request_head
from argus.gitsync.pr_mergeable import gh_pull_request_mergeable
from argus.gitsync.receipts import pull_request_ref

# A GitHub Actions job link: the repository and the job id are all gh needs.
_JOB_LINK = re.compile(r"https://([^/]+)/([^/]+/[^/]+)/actions/runs/\d+/job/(\d+)")
TAIL_LINES = 40


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pull-request", required=True, help="number or URL")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=int)
    parser.add_argument("--wait", required=True, type=int)
    parser.add_argument("--head", required=True, help="the pull request head commit")
    parser.add_argument("--draft", choices=("yes", "no"), default="no")
    parser.add_argument(
        "--launched", action="store_true", help="count one round; read nothing"
    )
    return parser


def _key(pull_request: str) -> str:
    """A file-name-safe key for one pull request: its trailing number."""
    tail = pull_request.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise ValueError(f"pull request {pull_request!r} has no number")
    return tail


def save_failed_log(
    run_dir: Path, key: str, failing: list[dict[str, str]]
) -> tuple[list[str], str | None]:
    """Save the first failing job's failed-step log; return its tail and path.

    Only a GitHub Actions job link is read. Any other link, or a gh call that
    fails or times out, gives no log: the task file then says "not saved".
    """
    for check in failing:
        match = _JOB_LINK.match(check.get("link", ""))
        if not match:
            continue
        host, repo, job = match.groups()
        target = repo if host == "github.com" else f"{host}/{repo}"
        argv = [gh_binary(), "run", "view", "--job", job, "--log-failed"]
        try:
            done = subprocess.run(
                [*argv, "--repo", target],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GH_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            return [], None
        if done.returncode:
            return [], None
        path = run_dir / f"repair-{key}.log"
        path.write_text(done.stdout, encoding="utf-8")
        return done.stdout.splitlines()[-TAIL_LINES:], str(path)
    return [], None


def issue_pull_requests(
    root: str | Path, unread: list[int] | None = None
) -> list[dict[str, object]]:
    """One row per open pull request from this repository on a recorded branch.

    The branch comes from the issue run folder: the claim's reservation and
    the frozen task it names, the same proof the issue checkpoint uses. A
    folder without that proof is skipped. A gh read that fails is named on
    stderr by issue number only and skipped. Its number is also appended to
    ``unread`` when that list is given, so the caller can stop the tick.
    """
    rows: list[dict[str, object]] = []
    for run_dir in sorted(conventions.runs_dir(root).glob("issue-*")):
        match = re.fullmatch(r"issue-([0-9]+)", run_dir.name)
        if match is None or not run_dir.is_dir():
            continue
        number = int(match.group(1))
        try:
            branch = issue_branch(number, issue_scope(root, number)[1][1])
        except (ConfigError, CoordinationError):
            continue
        found, _why = gh_pull_requests_for_branch(Path(root), branch)
        if found is None:
            print(f"repair: issue {number}: pull requests unread", file=sys.stderr)
            if unread is not None:
                unread.append(number)
            continue
        # gh_pull_requests_for_branch is the one filter: it keeps only this
        # repository's rows on the branch, so a fork's checks and logs never arrive.
        for pr in found:
            if pr["state"] != "OPEN":
                continue
            if pr["isDraft"] is None:
                # gh gave no draft flag, so the tick cannot tell whether to wait.
                print(f"repair: issue {number}: draft state unread", file=sys.stderr)
                if unread is not None:
                    unread.append(number)
                break
            rows.append(
                {
                    "issue": number,
                    "branch": branch,
                    "pull_request": pr["url"],
                    "draft": pr["isDraft"],
                }
            )
    return rows


def run(args: argparse.Namespace, now: float) -> tuple[int, dict[str, object]]:
    key = _key(args.pull_request)
    record_path = args.run_dir / f"repair-{key}.json"
    if args.launched:
        record = repair_round.for_head(repair_round.load_record(record_path), args.head)
        record["attempts"] = [*record.get("attempts", []), now]
        repair_round.save_record(record_path, record)
        return 0, {"action": "counted"}
    # The repository is the one the pull request URL names. The driver passes the
    # URL that gh listed for the issue branch; a bare number is refused by gh_checks.
    ref = pull_request_ref(args.pull_request) or {}
    rows, code, _detail = gh_pull_request_checks(
        args.run_dir,
        args.pull_request,
        ref.get("owner"),
        ref.get("repo"),
        ref.get("host"),
    )
    if rows is None:
        return 3, {"action": "unread", "read": "checks", "exit_code": code}
    # A conflict starts a round even when no check is red, so the merge state is
    # read on every tick. A failed read stops the tick, as a failed checks read does.
    mergeable = gh_pull_request_mergeable(
        args.run_dir,
        args.pull_request,
        ref.get("owner"),
        ref.get("repo"),
        ref.get("host"),
    )
    if mergeable is None:
        return 3, {"action": "unread", "read": "mergeable"}
    # The head is read only while a check is pending: the pending limit needs it,
    # and a failed read stops the tick like any other unread state.
    head: str | None = None
    if any(row.get("bucket") == "pending" for row in rows):
        head = gh_pull_request_head(
            args.run_dir,
            args.pull_request,
            ref.get("owner"),
            ref.get("repo"),
            ref.get("host"),
        )
        if head is None:
            return 3, {"action": "unread", "read": "head", "exit_code": -1}
    try:
        record = repair_round.for_head(repair_round.load_record(record_path), args.head)
    except ValueError:
        # A lost count must not read as zero attempts, so no round starts. The
        # notice names the record, so the cause is not the checks.
        message = (
            f"Needs You. The attempt record {record_path.name} of "
            f"{args.pull_request} cannot be read, so Argus starts no repair round "
            "for it. Action: move that file aside, then the next tick starts the count again."
        )
        return 0, {"action": "unreadable", "message": message}
    action, due = repair_round.decide(
        rows,
        record,
        now=now,
        attempts=args.attempts,
        wait=args.wait,
        mergeable=mergeable,
    )
    conflict = mergeable == "CONFLICTING"
    failing = repair_round.failing_checks(rows)
    out: dict[str, object] = {"action": action}
    if action == "wait":
        out["due"] = due
    elif action == "repair":
        task = args.run_dir / f"repair-{key}.md"
        tail, log_path = save_failed_log(args.run_dir, key, failing)
        task.write_text(
            repair_round.task_text(
                args.pull_request, failing, tail, log_path, conflict=conflict
            ),
            encoding="utf-8",
        )
        out["task"] = str(task)
    elif action == "escalate":
        record["escalated"] = True
        repair_round.save_record(record_path, record)
        out["message"] = repair_round.escalation_text(
            args.pull_request, args.attempts, failing, conflict=conflict
        )
    pending_path = args.run_dir / f"pending-{key}.json"
    out.update(pending_limit.step(pending_path, args.pull_request, head, rows, now))
    # A draft whose pending check passed the limit has raised its Needs You notice,
    # so it counts as escalated. One stuck check must not stall the driver.
    waiting = repair_round.waiting_reason(
        rows,
        draft=args.draft == "yes",
        mergeable=mergeable,
        escalated=(
            action == "escalate"
            or bool(record.get("escalated"))
            or pending_limit.noticed(pending_path, head)
        ),
    )
    if waiting:
        out["waiting"] = waiting
    return 0, out


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    # The driver also passes its configuration flags; configure_from_cli reads them.
    args, _config = build_parser().parse_known_args(argv)
    try:
        rc, out = run(args, time.time() if now is None else now)
    except ValueError as exc:
        print(f"repair_cli: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    return rc


if __name__ == "__main__":
    # gh_binary reads commands.gh, so the settings load before any gh call.
    conventions.configure_from_cli(sys.argv[1:])
    sys.exit(main())
