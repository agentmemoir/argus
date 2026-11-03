# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The refusal that a repair launch leaves in its run folder, and its take-back.

The session writes ``repair-<key>.refused`` when a launch runs no agent: three
lines, the head the launch was made on, the time of the refusal, and the reason.
The next call of the repair command takes that round back, once, and records the
refusal in the attempt record (``repair_round.refuse``). Nothing here calls gh or git.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.gitsync import repair_round


def take(
    run_dir: Path,
    record_path: Path,
    key: str,
    head: str,
    record: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    """Take back the round that a refused launch left in the run folder, once.

    A refusal for another head is dropped: that head's count was reset with its
    record. A refusal with no readable time is counted at ``now``.
    """
    path = run_dir / f"repair-{key}.refused"
    if not path.exists():
        return record
    head_line, at_line, reason = (
        path.read_text(encoding="utf-8").split("\n") + ["", "", ""]
    )[:3]
    if head_line == head:
        try:
            at = float(at_line)
        except ValueError:
            at = now
        record = repair_round.refuse(record, reason or "no reason recorded", at)
        repair_round.save_record(record_path, record)
    path.unlink()
    return record


__all__ = ["take"]
