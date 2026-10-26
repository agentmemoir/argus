# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Report phases with run work that no live claim covers.

The scan is read-only: one board read, and nothing is written. A phase with a
run directory but no record, an expired record, a record owned by another
machine, or a terminal record is work nobody is holding, so it is reported with
the reason. A live claim held by this machine, or by any machine when no
``machine_id`` is given, is not reported.
"""

import datetime as _datetime
from pathlib import Path
from typing import Any

from argus import conventions
from argus.board.core import (
    ACTIVE_STATUSES,
    EXPIRED_STATUS,
    TERMINAL_STATUSES,
    CoordinationUnavailable,
    _as_moment,
    _phase_key,
)
from argus.board.records import _claim_window_passed
from argus.phases.evidence import run_completed

#: Files whose presence in a phase run directory means the phase was run.
_RUN_MARKERS = ("run.json", "resume.json")


def _has_work(directory: Path) -> bool:
    if any((directory / name).is_file() for name in _RUN_MARKERS):
        return True
    return any(directory.glob("session-*.log"))


def scan_claims(
    repo: str | Path,
    store: Any,
    *,
    phase: str | int | None = None,
    machine_id: str | None = None,
    now: _datetime.datetime | str | None = None,
) -> dict[str, Any]:
    """List run-work phases with no live claim, each with the reason.

    Raises ``CoordinationUnavailable`` when the board cannot be read, so a
    broken read is never mistaken for a board with no claims. A pipeline
    configuration that cannot be resolved raises the same way: the run folder
    is not usable, which is the documented exit-2 contract, not an answer
    about claims.
    """
    wanted = None if phase is None else _phase_key(phase)[0]
    state, _commit = store.read()
    records = state["phases"]
    moment = _as_moment(now) or _datetime.datetime.now(_datetime.UTC)
    runs = conventions.runs_dir(repo)
    found: list[dict[str, Any]] = []
    for directory in sorted(runs.glob("phase-*")):
        if not directory.is_dir() or not _has_work(directory):
            continue
        key = directory.name[len("phase-") :]
        if not key.isdigit():
            continue
        if wanted is not None and key != wanted:
            continue
        try:
            completed = run_completed(repo, int(key))
        except ValueError as exc:
            # A malformed pipeline configuration makes the run folder unusable
            # for this phase. Name the configuration error and fail under the
            # documented contract instead of letting the traceback escape.
            raise CoordinationUnavailable(f"phase {key}: {exc}") from exc
        if completed:
            continue
        record = records.get(key)
        if record is None:
            found.append(
                {"phase": key, "machine_id": None, "status": None, "reason": "no_claim"}
            )
            continue
        owner = record.get("machine_id")
        status = record.get("status")
        if status in TERMINAL_STATUSES:
            reason = "terminal"
        elif status == EXPIRED_STATUS or (
            status in ACTIVE_STATUSES and _claim_window_passed(record, moment)
        ):
            reason = "expired"
        elif machine_id is not None and owner != machine_id:
            reason = "other_owner"
        else:
            continue
        found.append(
            {"phase": key, "machine_id": owner, "status": status, "reason": reason}
        )
    return {"state": "OK", "phases": found}


__all__ = ["scan_claims"]
