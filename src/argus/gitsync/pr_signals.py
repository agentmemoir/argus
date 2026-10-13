# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Watch a phase's pull request between driver ticks and record what changed.

The driver calls this on every tick that finds a phase waiting for review. It
reads the pull request, diffs the result against the last good snapshot, and
appends each change to the phase run folder. A failed read records nothing new:
the last good snapshot stays, and the failure is reported, never read as "no
change".
"""

from pathlib import Path
from typing import Any

from argus.conventions import phase_run_dir
from argus.gitsync.pr_signal_diff import diff_snapshots
from argus.gitsync.pr_signal_fetch import Fetch, Snapshot, fetch_snapshot
from argus.gitsync.pr_signal_store import (
    EVENTS_FILE,
    append_log,
    now,
    read_snapshot,
    snapshot_lock,
    write_snapshot,
)
from argus.gitsync.pr_signal_trust import Visibility, build_visibility
from argus.gitsync.receipts import phase_receipt, pull_request_ref
from argus.issues.transport import IssuesUnavailable


def watch_pull_request(
    run_dir: Path,
    ref: dict[str, Any],
    fetch: Fetch = fetch_snapshot,
    visibility: Visibility | None = None,
) -> dict[str, Any]:
    """Read the pull request once, record its changes, and report them."""
    snapshot, why = fetch(
        ref["owner"],
        ref["repo"],
        ref["number"],
        ref["host"],
        visibility or Visibility.closed(),
    )
    if snapshot is None:
        return {"ok": False, "state": "UNAVAILABLE", "reasons": [why], "events": []}
    snapshot_at: Snapshot = {
        **snapshot,
        "checked_at": now(),
        "pull_request": {
            "host": ref["host"],
            "owner": ref["owner"],
            "repo": ref["repo"],
            "number": ref["number"],
        },
    }
    with snapshot_lock(run_dir):
        events = diff_snapshots(read_snapshot(run_dir), snapshot)
        for event in events:
            event["at"] = snapshot_at["checked_at"]
        write_snapshot(run_dir, snapshot_at)
        append_log(run_dir, EVENTS_FILE, events)
    return {"ok": True, "state": "WATCHED", "reasons": [], "events": events}


def mode_signals(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Watch the pull request of a phase, or report that it has none yet."""
    root = Path(root).resolve()
    receipt = phase_receipt(root, int(phase))
    ref = pull_request_ref(receipt.get("pull_request")) if receipt else None
    if ref is None:
        return {"ok": True, "state": "NO-PULL-REQUEST", "reasons": [], "events": []}
    try:
        visibility = build_visibility(root)
    except IssuesUnavailable as exc:
        reason = f"trust settings cannot be read: {exc}"
        return {"ok": False, "state": "UNAVAILABLE", "reasons": [reason], "events": []}
    out = watch_pull_request(
        phase_run_dir(root, int(phase)), ref, visibility=visibility
    )
    out["phase"] = int(phase)
    return out
