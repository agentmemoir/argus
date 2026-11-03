# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The attempt count for a failed pull request listing of one issue run.

Argus gives one failed listing three attempts: now, after 10 minutes, and after
20 (PHILOSOPHY.md, section 3). While an issue is inside those attempts, the tick
holds before new work, so a failed read never looks like "no change"
(SECURITY.md, rule 2). The third failure raises one Needs You notice. From then
on the issue is left out of selection and out of the hold, and its reads go on at
the same spacing. A successful read clears the count and the exclusion.

The count is a CI repair attempt record (repair_round.load_record and
save_record), so its schedule is the one CI repair rounds use. It sits in the
issue's run folder under its own name, because an issue number and a pull request
number can be equal, and the pull request record is repair-<n>.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus import conventions
from argus.gitsync import repair_round

ATTEMPTS = 3
WAIT_S = 10 * 60
RECORD = "read-fault.json"


def record_path(root: str | Path, number: int) -> Path:
    """The count file for one issue: inside the issue's run folder."""
    return conventions.runs_dir(root) / f"issue-{number}" / RECORD


def _attempts(path: Path) -> list[float]:
    try:
        record: dict[str, Any] = repair_round.load_record(path)
    except (OSError, ValueError):
        # A damaged count reads as empty, so it cannot trap the next run.
        return []
    return [float(t) for t in record.get("attempts", [])]


def due(path: Path, now: float) -> bool:
    """Whether the next attempt is due: the first now, then WAIT_S after each one."""
    started = _attempts(path)
    return not started or now >= started[0] + len(started) * WAIT_S


def held(path: Path) -> bool:
    """Whether the count is inside its attempts, so the tick holds before new work."""
    return 0 < len(_attempts(path)) < ATTEMPTS


def excluded(path: Path) -> bool:
    """Whether the attempts are spent, so selection and the hold leave the issue out."""
    return len(_attempts(path)) >= ATTEMPTS


def failed(path: Path, number: int, reason: str, now: float) -> str | None:
    """Count one failed attempt. The third one returns the one Needs You text."""
    started = [*_attempts(path), now]
    repair_round.save_record(path, {"attempts": started, "reason": reason})
    if len(started) != ATTEMPTS:
        return None
    return (
        f"Needs You. Argus could not list the pull requests of issue {number} "
        f"after {ATTEMPTS} attempts (now, after 10 minutes, and after 20): {reason}. "
        "Action: fix the cause of that gh failure on this host. Argus keeps trying "
        "on its own, and the issue returns to normal once a read succeeds."
    )


def cleared(path: Path) -> bool:
    """Drop the count after a good read. True when the issue had failed attempts."""
    had = bool(_attempts(path))
    path.unlink(missing_ok=True)
    return had
