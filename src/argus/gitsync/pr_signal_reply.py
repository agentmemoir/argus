# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reply to or react to one review comment a phase's pull request recorded.

This is the one write path an agent gets for pull request threads. It writes
nothing unless the comment is a top-level comment in the recorded snapshot, a
fresh read still shows it on the same pull request, and the read succeeded.
It never merges, pushes, or edits a pull request body.
"""

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.conventions import phase_run_dir
from argus.gitsync.gh_gate import _gh
from argus.gitsync.pr_signal_fetch import Fetch, Snapshot, fetch_snapshot
from argus.gitsync.pr_signal_store import WRITES_FILE, append_log, now, read_snapshot
from argus.gitsync.pr_signal_trust import Visibility, build_visibility
from argus.gitsync.receipts import phase_receipt, pull_request_ref
from argus.issues.transport import IssuesUnavailable

REACTIONS = frozenset(
    {"+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"}
)
MAX_REPLY_CHARS = 4000
REPLY_CHECK_TIMEOUT = 30
SECRET_PATTERN = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
REDACTION = "[redacted]"


def _refused(reason: str) -> dict[str, Any]:
    return {"ok": False, "state": "REFUSED", "reasons": [reason], "events": []}


def _post(argv: list[str], host: str | None) -> tuple[bool, str]:
    if host and host != "github.com":
        argv = ["--hostname", host, *argv]
    done, why = _gh(["api", "-X", "POST", *argv])
    if done is None:
        return False, why
    if done.returncode != 0:
        return False, f"gh api exited {done.returncode}"
    return True, ""


def _recorded_reason(recorded: Snapshot | None, cid: str, ref: dict[str, Any]) -> str:
    """Why the recorded snapshot does not allow a write to ``cid``, or ""."""
    if recorded is None:
        return "no recorded snapshot: watch the pull request first"
    bound = {
        "host": ref["host"],
        "owner": ref["owner"],
        "repo": ref["repo"],
        "number": ref["number"],
    }
    if recorded.get("pull_request") != bound:
        return (
            "the recorded snapshot is for a different pull request; run signals first"
        )
    comments = recorded.get("comments")
    if not isinstance(comments, dict):
        return "comment is not in the recorded snapshot"
    row = cast("dict[str, Any]", comments).get(cid)
    if row is None:
        return "comment is not in the recorded snapshot"
    if row.get("allowed") is not True:
        return "the comment's author is not trusted, so the agent may not answer it"
    if row["in_reply_to"] is not None:
        return "replies to replies are not supported"
    return ""


def _host_reply_refusal(text: str) -> str:
    """Why the host reply check refused the draft reply, or "" when it did not."""
    command = conventions.setting("commands.reply-check", None)
    if not command:
        return ""
    try:
        argv = shlex.split(command)
    except ValueError:
        return "the host reply check could not run; nothing was posted"
    if not argv:
        return "the host reply check could not run; nothing was posted"
    try:
        done = subprocess.run(
            argv,
            input=text,
            capture_output=True,
            text=True,
            timeout=REPLY_CHECK_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return "the host reply check could not run; nothing was posted"
    if done.returncode != 0:
        return "the host reply check refused this reply; nothing was posted"
    return ""


def _redact(text: str) -> str:
    """Replace every secret-looking span with the fixed redaction marker.

    The scan never refuses outbound text: a hit is sanitized so the reply can
    still go out, and the substitution is deterministic, so it cannot fail.
    """
    return SECRET_PATTERN.sub(REDACTION, text)


def _text_reason(text: str) -> str:
    """Why a reply text may not be posted, or an empty string."""
    if len(text) > MAX_REPLY_CHARS:
        return f"reply is longer than {MAX_REPLY_CHARS} characters; nothing was posted"
    return ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _already_posted(run_dir: Path, cid: str, digest: str) -> bool:
    """Whether this run already posted this exact reply to this comment."""
    try:
        lines = (run_dir / WRITES_FILE).read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            parsed: object = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        row = cast("dict[str, Any]", parsed)
        if (
            row.get("kind") == "reply"
            and row.get("comment") == cid
            and row.get("text_sha256") == digest
            and row.get("state") == "POSTED"
        ):
            return True
    return False


def reply_to_comment(
    run_dir: Path,
    ref: dict[str, Any],
    comment_id: int,
    text: str | None = None,
    reaction: str | None = None,
    fetch: Fetch = fetch_snapshot,
    visibility: Visibility | None = None,
) -> dict[str, Any]:
    """Post one reply or one reaction on a recorded comment, or refuse."""
    if (text is None) == (reaction is None):
        return _refused("give exactly one of a reply text or a reaction")
    if reaction is not None and reaction not in REACTIONS:
        return _refused(f"reaction must be one of {sorted(REACTIONS)}")
    if text is not None:
        # The outbound secret scan sanitizes rather than refuses: the reply goes
        # out with each secret-looking span replaced, so a hit never stops the run.
        text = _redact(text)
    text_refusal = _text_reason(text) if text is not None else ""
    if text_refusal:
        return _refused(text_refusal)
    cid = str(comment_id)
    reason = _recorded_reason(read_snapshot(run_dir), cid, ref)
    if reason:
        return _refused(reason)
    fresh, why = fetch(
        ref["owner"],
        ref["repo"],
        ref["number"],
        ref["host"],
        visibility or Visibility.closed(),
    )
    if fresh is None:
        return _refused(f"could not re-read the pull request: {why}")
    comments = fresh.get("comments")
    rows: dict[str, Any] = (
        cast("dict[str, Any]", comments) if isinstance(comments, dict) else {}
    )
    if cid not in rows:
        return _refused("comment is no longer on the pull request")
    if rows[cid].get("allowed") is not True:
        return _refused(
            "the comment's author is not trusted, so the agent may not answer it"
        )
    if text is not None:
        host_refusal = _host_reply_refusal(text)
        if host_refusal:
            return _refused(host_refusal)
    base = f"repos/{ref['owner']}/{ref['repo']}/pulls"
    digest = _digest(text) if text is not None else ""
    if text is not None:
        posted = any(
            row["in_reply_to"] == comment_id and row["body"] == text
            for row in rows.values()
        )
        if posted or _already_posted(run_dir, cid, digest):
            return {"ok": True, "state": "ALREADY-POSTED", "reasons": [], "events": []}
        ok, why = _post(
            [f"{base}/{ref['number']}/comments/{cid}/replies", "-f", f"body={text}"],
            ref["host"],
        )
        kind = "reply"
    else:
        ok, why = _post(
            [f"{base}/comments/{cid}/reactions", "-f", f"content={reaction}"],
            ref["host"],
        )
        kind = "reaction"
    state = "POSTED" if ok else "FAILED"
    append_log(
        run_dir,
        WRITES_FILE,
        [
            {
                "at": now(),
                "kind": kind,
                "comment": cid,
                "state": state,
                "why": why,
                "text_sha256": digest,
            }
        ],
    )
    return {"ok": ok, "state": state, "reasons": [why] if why else [], "events": []}


def mode_signal_reply(
    root: str | Path,
    phase: int | str,
    comment_id: int,
    text: str | None = None,
    reaction: str | None = None,
) -> dict[str, Any]:
    """Reply to or react to a comment on the phase's pull request."""
    root = Path(root).resolve()
    receipt = phase_receipt(root, int(phase))
    ref = pull_request_ref(receipt.get("pull_request")) if receipt else None
    if ref is None:
        return _refused("the phase has no pull request")
    try:
        visibility = build_visibility(root)
    except IssuesUnavailable as exc:
        return _refused(f"trust settings cannot be read: {exc}")
    out = reply_to_comment(
        phase_run_dir(root, int(phase)),
        ref,
        comment_id,
        text=text,
        reaction=reaction,
        visibility=visibility,
    )
    out["phase"] = int(phase)
    return out
