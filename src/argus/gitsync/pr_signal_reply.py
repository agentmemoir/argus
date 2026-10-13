# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Reply to or react to one review comment a phase's pull request recorded.

This is the one write path an agent gets for pull request threads. It writes
nothing unless the comment is a top-level comment in the recorded snapshot, a
fresh read still shows it on the same pull request, and the read succeeded.
It never merges, pushes, or edits a pull request body.
"""

from pathlib import Path
from typing import Any

from argus.conventions import phase_run_dir
from argus.gitsync.gh_gate import _gh
from argus.gitsync.pr_signal_fetch import Fetch, Snapshot, fetch_snapshot
from argus.gitsync.pr_signal_store import WRITES_FILE, append_log, now, read_snapshot
from argus.gitsync.receipts import phase_receipt, pull_request_ref

REACTIONS = frozenset(
    {"+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"}
)


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


def _recorded_reason(recorded: Snapshot | None, cid: str) -> str:
    """Why the recorded snapshot does not allow a write to ``cid``, or ""."""
    if recorded is None:
        return "no recorded snapshot: watch the pull request first"
    row = recorded["comments"].get(cid)
    if row is None:
        return "comment is not in the recorded snapshot"
    if row["in_reply_to"] is not None:
        return "replies to replies are not supported"
    return ""


def reply_to_comment(
    run_dir: Path,
    ref: dict[str, Any],
    comment_id: int,
    text: str | None = None,
    reaction: str | None = None,
    fetch: Fetch = fetch_snapshot,
) -> dict[str, Any]:
    """Post one reply or one reaction on a recorded comment, or refuse."""
    if (text is None) == (reaction is None):
        return _refused("give exactly one of a reply text or a reaction")
    if reaction is not None and reaction not in REACTIONS:
        return _refused(f"reaction must be one of {sorted(REACTIONS)}")
    cid = str(comment_id)
    reason = _recorded_reason(read_snapshot(run_dir), cid)
    if reason:
        return _refused(reason)
    fresh, why = fetch(ref["owner"], ref["repo"], ref["number"], ref["host"])
    if fresh is None:
        return _refused(f"could not re-read the pull request: {why}")
    if cid not in fresh["comments"]:
        return _refused("comment is no longer on the pull request")
    base = f"repos/{ref['owner']}/{ref['repo']}/pulls"
    if text is not None:
        posted = any(
            row["in_reply_to"] == comment_id and row["body"] == text
            for row in fresh["comments"].values()
        )
        if posted:
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
        [{"at": now(), "kind": kind, "comment": cid, "state": state, "why": why}],
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
    out = reply_to_comment(
        phase_run_dir(root, int(phase)), ref, comment_id, text=text, reaction=reaction
    )
    out["phase"] = int(phase)
    return out
