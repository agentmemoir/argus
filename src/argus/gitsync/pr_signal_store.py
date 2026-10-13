# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase run folder's files for pull request signals: the last snapshot and two logs."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SNAPSHOT_FILE = "pr-signals.json"
EVENTS_FILE = "pr-signal-events.jsonl"
WRITES_FILE = "pr-signal-writes.jsonl"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_snapshot(run_dir: Path) -> dict[str, Any] | None:
    """The last recorded snapshot, or None when none is readable."""
    try:
        data = json.loads((run_dir / SNAPSHOT_FILE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_snapshot(run_dir: Path, snapshot: dict[str, Any]) -> None:
    """Replace the recorded snapshot in one step, so a reader never sees half of it."""
    path = run_dir / SNAPSHOT_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def append_log(run_dir: Path, name: str, rows: list[dict[str, Any]]) -> None:
    """Append one JSON line per row to a log in the run folder."""
    if not rows:
        return
    with (run_dir / name).open("a") as log:
        for row in rows:
            log.write(json.dumps(row, sort_keys=True) + "\n")
