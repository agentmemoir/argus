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
from argus.gitsync import issue_read_fault
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
ATTEMPT_LIMIT = issue_read_fault.ATTEMPTS
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


def _failed(number: int, attempts: Path, record: dict[str, Any], cause: str) -> str:
    """Count one failed attempt. Returns WAITS, or the Needs You line at the limit."""
    record["attempts"].append(time.time())
    record["cause"] = cause
    try:
        save_record(attempts, record)
    except OSError:
        return _give_up(number, "the attempt record cannot be written")
    if len(record["attempts"]) >= ATTEMPT_LIMIT:
        return _give_up(number, cause)
    return WAITS


def _clear(attempts: Path) -> None:
    """Drop the count after a close that worked."""
    try:
        attempts.unlink(missing_ok=True)
    except OSError as exc:
        print(f"close count not cleared: {exc}", file=sys.stderr)


def _read_count(path: Path) -> dict[str, Any] | None:
    """The attempt record, or None when it cannot be read or its attempt times are bad.

    The times are checked by the same rule as the failed-read count, so a record the
    schedule cannot read is spent here too.
    """
    if issue_read_fault.damaged(path):
        return None
    record = load_record(path)
    return record if isinstance(record.get("attempts"), list) else None


def spent(root: Path, number: int) -> list[dict[str, str]]:
    """The Needs You line that stops selection for a claim with no attempts left.

    The count is spent after its third attempt. An unreadable count counts as spent
    too, so the queue moves on. The text is the one the close sends, and the notice
    marker in the run folder keeps it to one send. Empty while the close may still try.
    """
    run = reservation_file(root, number, kind="issue").parent
    record = _read_count(run / ATTEMPTS_FILE)
    if record is None:
        text = _give_up(number, "the attempt record is unreadable")
    elif len(record["attempts"]) < ATTEMPT_LIMIT:
        return []
    else:
        text = _give_up(number, str(record.get("cause", "unknown")))
    return [{"key": f"completed-{number}", "text": text}]


def close_no_change(
    repo: Path, run_dir: Path, number: int, reason: str, machine: str
) -> str:
    """Close the issue once, and answer ``closed``, ``waits``, or a Needs You line.

    The caller passes the checked reason, or nothing when the checks refused. The
    issue must still be open and recorded by this machine. Each attempt that does not
    close the issue counts one. An attempt runs only when it is due: now, then ten
    minutes after the first, then twenty minutes after it. A tick with no due attempt
    closes nothing and counts nothing. The third answers the one Needs You line, and
    selection then leaves the claim out. A close that works clears the count.
    ``gh issue close`` posts its comment before it closes, so a close that failed can
    leave the comment, and the retry finds it by its marker and posts no second one.
    """
    if (run_dir / CLOSED_FILE).exists():
        return CLOSED
    attempts = run_dir / ATTEMPTS_FILE
    record = _read_count(attempts)
    if record is None:
        # A lost count must not look like zero attempts.
        return _give_up(number, "the attempt record is unreadable")
    if len(record["attempts"]) >= ATTEMPT_LIMIT:
        return _give_up(number, str(record.get("cause", "unknown")))
    if not issue_read_fault.due(attempts, time.time()):
        return WAITS
    if not reason:
        return _failed(number, attempts, record, "the no-change checks did not pass")
    try:
        client = IssueClient()
        state, _ = ReservationStore(repo, issues=True).read()
        held: dict[str, Any] | None = state["phases"].get(f"{number:06d}")
        if held is None or held.get("status") not in RECORDED:
            return _failed(number, attempts, record, "the claim is not recorded")
        if held.get("machine_id") != machine:
            return WAITS
        issue_state = client.view(number)["state"].lower()
        if issue_state == CLOSED:
            return CLOSED
        if issue_state != "open":
            return _failed(number, attempts, record, "the issue is not open")
        digest = hashlib.sha256(str(held["reservation_id"]).encode()).hexdigest()
        marker = f"<!-- argus-no-change:issue={number}:sha256={digest} -->"
        posted = any(
            str(c.get("body", "")).endswith(marker) for c in client.comments(number)
        )
    except IssuesUnavailable as exc:
        return _failed(number, attempts, record, exc.reason)
    except (CoordinationError, ValueError, KeyError, TypeError):
        return _failed(number, attempts, record, "the claim could not be read")
    comment = closing_comment(reason, marker, show_reason=publishable(reason, machine))
    try:
        client.close(number, CLOSE_REASON, None if posted else comment)
    except IssuesUnavailable as exc:
        return _failed(number, attempts, record, exc.reason)
    _clear(attempts)
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
