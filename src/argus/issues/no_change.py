# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Close an issue whose run Argus checked as a no-change ending."""

import hashlib
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

from argus import conventions
from argus.board.core import EXPIRED_STATUS, CoordinationError
from argus.board.files import reservation_file
from argus.board.reservations import ACTIVE_STATUSES, ReservationStore
from argus.gitsync.pr_signal_reply import host_reply_refusal
from argus.gitsync.repair_round import load_record, save_record
from argus.issues import claims
from argus.issues.client import IssueClient
from argus.issues.completed_run import (
    NO_CHANGE_FILE,
    checked_no_change_reason,
    pull_request_verdict,
)
from argus.issues.transport import IssuesUnavailable
from argus.phases.runstate import state_in

# Written after a close that worked. An open issue with this file was reopened, and
# the next tick releases its run. A failed close leaves no file, so it is retried.
CLOSED_FILE = "no-change-closed"
ATTEMPTS_FILE = "no-change-close-attempts.json"
ATTEMPT_LIMIT = 3
# GitHub's closing reason for work that will not happen because the premise is stale.
CLOSE_REASON = "not planned"
CLOSED = "closed"
WAITS = "waits"
# A claim that lapsed or finished stays this machine's record until it is released.
ENDED = ("completed", EXPIRED_STATUS)
RECORDED = {*ACTIVE_STATUSES, *ENDED}
ABSOLUTE_PATH = re.compile(r"(?<![\w.:/-])(?:~|/[\w.-]+)/")


def publishable(reason: str, machine: str) -> bool:
    """Whether the agent's reason may go on a public issue.

    The token scan already ran on it, and it matches token shapes only. The host's
    ``commands.reply-check`` is what knows the host's names, so a host with no check
    posts no reason. This code adds no list of names. It refuses any absolute path,
    and this machine's own id, host name, and home folder name.
    """
    if not conventions.setting("commands.reply-check", None):
        return False
    if host_reply_refusal(reason) or ABSOLUTE_PATH.search(reason):
        return False
    own = {machine, socket.gethostname().split(".")[0], Path.home().name}
    return not any(name and name.casefold() in reason.casefold() for name in own)


def closing_comment(reason: str, marker: str, *, show_reason: bool) -> str:
    """The one public comment. Backticks go, so the reason stays one inert code span."""
    span = reason.replace("`", "'")
    said = (
        f"The agent's reason, which is its claim. Argus did not check it: `{span}`"
        if show_reason
        else "The agent's reason is not shown, because no check passed it for "
        "public text."
    )
    return (
        "Argus closed this issue. The run ended with no code change.\n\n"
        "Argus checked:\n\n"
        "- The issue branch has zero commits past its base.\n"
        "- The working tree is clean.\n"
        "- No Argus pull request exists for this issue.\n\n"
        f"{said}\n\n"
        f"To undo this, reopen the issue. Argus then starts a new run.\n\n{marker}"
    )


def _give_up(number: int, cause: str) -> str:
    return (
        f"Needs You. Issue #{number} ends with no pull request and no code change, and "
        f"Argus could not close it after {ATTEMPT_LIMIT} attempts ({cause}). "
        "Action: close the issue. Check: the claim label is gone after the next tick."
    )


def close_no_change(
    repo: Path, run_dir: Path, number: int, reason: str, machine: str
) -> str:
    """Close the issue once, and answer ``closed``, ``waits``, or a Needs You line.

    The caller has checked the reason and the pull request verdict. The issue must
    still be open and recorded by this machine. A failed read writes nothing and
    raises nothing new. A failed close counts one attempt. ``gh issue close`` posts
    its comment before it closes, so a close that failed can leave the comment, and
    the retry finds it by its marker and posts no second one.
    """
    if (run_dir / CLOSED_FILE).exists():
        return CLOSED
    attempts = run_dir / ATTEMPTS_FILE
    try:
        record = load_record(attempts)
    except (OSError, ValueError):
        # A lost count must not look like zero attempts.
        return _give_up(number, "the attempt record is unreadable")
    if len(record["attempts"]) >= ATTEMPT_LIMIT:
        return _give_up(number, str(record.get("cause", "unknown")))
    try:
        client = IssueClient()
        state, _ = ReservationStore(repo, issues=True).read()
        held: dict[str, Any] | None = state["phases"].get(f"{number:06d}")
        if (
            held is None
            or held.get("machine_id") != machine
            or held.get("status") not in RECORDED
        ):
            return WAITS
        issue_state = client.view(number)["state"].lower()
        if issue_state == CLOSED:
            return CLOSED
        if issue_state != "open":
            return WAITS
        digest = hashlib.sha256(str(held["reservation_id"]).encode()).hexdigest()
        marker = f"<!-- argus-no-change:issue={number}:sha256={digest} -->"
        posted = any(
            str(c.get("body", "")).endswith(marker) for c in client.comments(number)
        )
    except (IssuesUnavailable, CoordinationError, ValueError, KeyError, TypeError):
        return WAITS
    comment = closing_comment(reason, marker, show_reason=publishable(reason, machine))
    try:
        client.close(number, CLOSE_REASON, None if posted else comment)
    except IssuesUnavailable as exc:
        record["attempts"].append(time.time())
        record["cause"] = exc.reason
        try:
            save_record(attempts, record)
        except OSError:
            return _give_up(number, "the attempt record cannot be written")
        if len(record["attempts"]) >= ATTEMPT_LIMIT:
            return _give_up(number, exc.reason)
        return WAITS
    try:
        (run_dir / CLOSED_FILE).write_text("closed\n", encoding="utf-8")
    except OSError as exc:
        print(f"issue {number}: close not recorded: {exc}", file=sys.stderr)
    return CLOSED


def close_lapsed(
    root: Path, machine: str, records: dict[str, Any]
) -> tuple[list[dict[str, str]], set[str]]:
    """Close each checked no-change run whose claim has ended; a label keeps it held.

    The selector skips such an issue, so the driver's hold never runs for it. The same
    checks run here, before this tick's notices and release. Returns the Needs You
    lines this raised, and the notice keys these runs replace.
    """
    notices: list[dict[str, str]] = []
    replaced: set[str] = set()
    for key, record in sorted(records.items()):
        number = int(key)
        run = reservation_file(root, number, kind="issue").parent
        status = record.get("status")
        if (
            record.get("machine_id") != machine
            or status not in ENDED
            or not (run / NO_CHANGE_FILE).exists()
            or (run / CLOSED_FILE).exists()
            or state_in(run) != "completed"
        ):
            continue
        frozen = claims.snapshot(root, number, record)
        if frozen is None:
            continue
        try:
            if pull_request_verdict(number, frozen.name) != "none":
                continue
        except IssuesUnavailable:
            continue
        reason = checked_no_change_reason(root, run, number, frozen.name)
        if not reason:
            continue
        replaced.add(f"{status}-{number}")
        outcome = close_no_change(root, run, number, reason, machine)
        if outcome.startswith("Needs You."):
            notices.append({"key": f"completed-{number}", "text": outcome})
    return notices, replaced
