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
driver's own state folder, which is the driver's run folder plus driver.state.
That folder is not an issue run folder, so the leftover sweep never stages it
into the shared board. Its name is read-fault-issue-<n>.json, apart from the
pull request record repair-<n>.json.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.gitsync import repair_round

ATTEMPTS = 3
WAIT_S = 10 * 60


def record_path(root: str | Path, number: int) -> Path:
    """The count file for one issue, in the driver's own state folder."""
    state = conventions.runs_dir(root) / conventions.setting("driver.state")
    return state / f"read-fault-issue-{number}.json"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read(path: Path) -> tuple[list[float], bool]:
    """The recorded attempt times, and whether the file is valid. Missing is valid.

    A value that is not a finite number, or that a float cannot hold, makes the file
    invalid too. NaN or Infinity would never come due, and a huge integer would raise
    out of the tick.
    """
    if not path.exists():
        return [], True
    try:
        record = repair_round.load_record(path)
        raw: object = record.get("attempts", [])
        if not isinstance(raw, list):
            return [], False
        items = cast("list[object]", raw)
        if not all(_number(t) for t in items):
            return [], False
        times = [float(cast("float", t)) for t in items]
    except (OSError, ValueError, OverflowError):
        return [], False
    if not all(math.isfinite(t) for t in times):
        return [], False
    # An attempt is recorded when it happens, so a time well after the clock is wrong.
    if any(t > time.time() + ATTEMPTS * WAIT_S for t in times):
        return [], False
    return times, True


def damaged(path: Path) -> bool:
    """Whether the count file holds anything other than a list of attempt times."""
    return not _read(path)[1]


def reset(path: Path) -> None:
    """Replace a damaged count with an empty one, so the damage is reported once."""
    repair_round.save_record(path, {"attempts": []})


def due(path: Path, now: float) -> bool:
    """Whether the next attempt is due: the first now, then WAIT_S after each one."""
    started, _ok = _read(path)
    return not started or now >= started[0] + len(started) * WAIT_S


def held(path: Path) -> bool:
    """Whether the count is inside its attempts, so the tick holds before new work."""
    return 0 < len(_read(path)[0]) < ATTEMPTS


def excluded(path: Path) -> bool:
    """Whether the attempts are spent, so selection and the hold leave the issue out."""
    return len(_read(path)[0]) >= ATTEMPTS


def failed(path: Path, number: int, reason: str, now: float) -> dict[str, str] | None:
    """Count one failed attempt. The third one returns the one Needs You notice.

    The notice is saved in the count file, with the count. A crash before it is sent
    leaves it there, and notice() returns it again on the next tick.
    """
    started = [*_read(path)[0], now]
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"attempts": started, "reason": reason}
    if len(started) == ATTEMPTS:
        text = (
            f"Needs You. Argus could not list the pull requests of issue {number} "
            f"after {ATTEMPTS} attempts (now, after 10 minutes, and after 20): "
            f"{reason}. Action: fix the cause of that gh failure on this host. Argus "
            "keeps trying on its own, and the issue returns to normal once a read "
            "succeeds."
        )
        record["notice"] = {
            "key": f"read-fault-{number}-{int(started[0])}",
            "text": text,
        }
    elif len(started) > ATTEMPTS:
        record["notice"] = repair_round.load_record(path).get("notice")
    repair_round.save_record(path, record)
    return record["notice"] if len(started) == ATTEMPTS else None


def notice(path: Path) -> dict[str, str] | None:
    """The saved Needs You notice, while it is owed. The shell sends it once per key."""
    if not _read(path)[1] or not path.exists():
        return None
    return repair_round.load_record(path).get("notice")


def cleared(path: Path) -> bool:
    """Drop the count after a good read. True when the issue had failed attempts."""
    had = bool(_read(path)[0])
    path.unlink(missing_ok=True)
    return had
