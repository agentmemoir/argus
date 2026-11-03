# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The refusal that a repair launch leaves in its run folder, and its take-back.

The session writes ``repair-<key>.refused`` when a launch runs no agent: three
lines, the head the launch was made on, the time of the refusal, and the reason.
The next call of the repair command takes that round back, once, and records the
refusal in the attempt record (``repair_round.refuse``). Nothing here calls gh or git.

A glide launch is a refusal only with proof that no stage started. glide writes a
failed ``result.json`` when its startup refuses the run, and that is the one change
to the run folder. Any other new, changed, or removed file, or any unclear record,
means a stage may have run, so the launch counts as a round.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from argus.gitsync import repair_round

RESULT = "result.json"


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
    # The file goes first: if the record was saved and the removal failed, the next
    # read would take the same round back again. Lost here, the round stays counted.
    path.unlink()
    if head_line == head:
        try:
            at = float(at_line)
        except ValueError:
            at = now
        record = repair_round.refuse(record, reason or "no reason recorded", at)
        repair_round.save_record(record_path, record)
    return record


def snapshot(folder: Path) -> dict[str, list[int]]:
    """Each file under the run folder, with its size and modification time."""
    if not folder.is_dir():
        return {}
    found: dict[str, list[int]] = {}
    for path in folder.rglob("*"):
        if path.is_file():
            stat = path.stat()
            found[path.relative_to(folder).as_posix()] = [
                stat.st_size,
                stat.st_mtime_ns,
            ]
    return found


def proven_refusal(folder: Path, before: dict[str, list[int]]) -> bool:
    """Whether a glide launch ran no stage: only its failed startup record changed.

    The one change allowed is a new or rewritten ``result.json`` whose state is
    ``failed``. Any other change, a missing folder, or an unreadable record returns
    False, so the launch counts as a round.
    """
    after = snapshot(folder)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    if changed != {RESULT}:
        return False
    try:
        record: object = json.loads((folder / RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict):
        return False
    data = cast("dict[str, object]", record)
    return data.get("state") == "failed"


def save_snapshot(folder: Path, out: Path) -> None:
    """Write the run folder's snapshot before a launch, for proven_refusal."""
    out.write_text(json.dumps(snapshot(folder), sort_keys=True), encoding="utf-8")


def refused_by_proof(folder: Path, before_path: Path) -> bool:
    """Read the snapshot taken before the launch, then apply proven_refusal."""
    before: dict[str, list[int]] = json.loads(before_path.read_text(encoding="utf-8"))
    return proven_refusal(folder, before)


__all__ = [
    "proven_refusal",
    "refused_by_proof",
    "save_snapshot",
    "snapshot",
    "take",
]
