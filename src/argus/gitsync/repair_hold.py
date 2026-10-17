# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The hold tally: why a repair waits, counted per day so a trend can be read."""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from argus.conventions import runs_dir, setting

# How many days of hold reasons the counter keeps. Enough to see a trend; small
# enough that the file stays a few lines.
_HOLD_DAYS_KEPT = 30


def count_hold(root: Path, reason: str, now: float) -> None:
    """Add one to today's count for ``reason``, so reads that fail can be counted.

    The repair log already prints each hold with its reason; this keeps a tally
    under the driver's state so a day's reasons can be read without grepping.
    """
    path = runs_dir(root) / setting("driver.state") / "repair" / "holds.json"
    day = datetime.fromtimestamp(now, UTC).date().isoformat()
    counts: dict[str, dict[str, int]] = {}
    with contextlib.suppress(OSError, ValueError):
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for day_key, value in cast("dict[object, object]", loaded).items():
                if not isinstance(value, dict):
                    continue
                counts[str(day_key)] = {
                    str(reason_key): count
                    for reason_key, count in cast("dict[object, object]", value).items()
                    if isinstance(count, int)
                }
    today = counts.setdefault(day, {})
    today[reason] = today.get(reason, 0) + 1
    for old in sorted(counts)[:-_HOLD_DAYS_KEPT]:
        del counts[old]
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts, sort_keys=True) + "\n", encoding="utf-8")


def waiting_hold(
    root: Path,
    path: Path,
    record: dict[str, Any],
    head: str | None,
    now: float,
    detail: str,
) -> dict[str, Any] | None:
    """A HOLD naming ``detail`` until ``repair.wait`` runs out, or ``None``.

    ``head`` is the pull request head the wait is keyed to; a new head restarts
    the wait. ``None`` means the head could not be read, so the wait already
    running is kept rather than restarted on every failing read.
    """
    # Imported here because `repair` imports this module: at module level the
    # two imports would meet.
    from argus.gitsync.repair import save_record

    wait = setting("repair.wait")
    since = record["waiting_since"]
    if since is None or (head is not None and record["head"] != head):
        since = now
        record["waiting_since"] = since
        if head is not None:
            record["head"] = head
        save_record(path, record)
    waited = int(now - since)
    if wait <= 0 or waited >= wait:
        return None
    count_hold(root, detail, now)
    return {
        "state": "HOLD",
        "summary": f"repair HOLD: {detail} (waited {waited}s of {wait}s)",
    }


__all__ = ["count_hold", "waiting_hold"]
