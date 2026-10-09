# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The board state file: its shape, reading, writing, and record view."""

import json
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.board.core import (
    COORDINATION_VERSION,
    PHASE_KEY_RE,
    CoordinationError,
    CoordinationUnavailable,
    _phase_key,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
    _validate_status,
)
from argus.conventions import board_repo


def state_rel() -> Path:
    configured = conventions.setting("board.file")
    path = Path(configured)
    if path.is_absolute():
        board = board_repo()
        if board is None:
            raise CoordinationError("reservation board repository is not configured")
        try:
            path = path.resolve().relative_to(Path(board).resolve())
        except ValueError as exc:
            raise CoordinationError(
                "coordination file must stay inside its configured repository"
            ) from exc
    if not path.parts or ".." in path.parts or path.parts[0] == ".git":
        raise CoordinationError(
            "coordination file must be a safe repository-relative path"
        )
    return path


def state_path(repo: str | Path) -> Path:
    root = Path(repo).resolve()
    path = root / state_rel()
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise CoordinationError(
            "coordination file must stay inside its repository"
        ) from exc
    return path


def _empty_state() -> dict[str, Any]:
    phases: dict[str, Any] = {}
    return {"version": COORDINATION_VERSION, "phases": phases}


def _validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise CoordinationUnavailable("coordination state is not an object")
    state = cast("dict[str, Any]", state)
    if state.get("version") != COORDINATION_VERSION:
        raise CoordinationUnavailable(
            f"unsupported coordination state version: {state.get('version')!r}"
        )
    phases = state.get("phases")
    if not isinstance(phases, dict):
        raise CoordinationUnavailable("coordination state.phases is not an object")
    phases = cast("dict[Any, Any]", phases)
    for key, record in phases.items():
        if not isinstance(key, str) or not PHASE_KEY_RE.fullmatch(key):
            raise CoordinationUnavailable(f"invalid phase key in state: {key!r}")
        if not isinstance(record, dict):
            raise CoordinationUnavailable(f"phase {key} record is not an object")
        record = cast("dict[str, Any]", record)
        for field in ("status", "machine_id", "reservation_id"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise CoordinationUnavailable(f"phase {key} has an invalid {field}")
        _validate_machine_id(record["machine_id"])
        _validate_reservation_id(record["reservation_id"])
        _validate_status(record["status"])
        _validate_generation(record.get("generation"))
        for field in ("claimed_at", "heartbeat_at", "expires_at", "run_id"):
            if (
                field in record
                and record[field] is not None
                and not isinstance(record[field], str)
            ):
                raise CoordinationUnavailable(f"phase {key} has an invalid {field}")
        if "base_revisions" in record and not isinstance(
            record["base_revisions"], dict
        ):
            raise CoordinationUnavailable(f"phase {key} has invalid base_revisions")
    publication = state.get("publication")
    if publication is not None:
        if not isinstance(publication, dict):
            raise CoordinationUnavailable("publication lock is not an object")
        publication = cast("dict[str, Any]", publication)
        for field in ("machine_id", "reservation_id", "phase", "generation"):
            if field not in publication:
                raise CoordinationUnavailable(f"publication lock is missing {field}")
        _validate_machine_id(publication["machine_id"])
        _validate_reservation_id(publication["reservation_id"])
        _phase_key(publication["phase"])
        _validate_generation(publication["generation"])
    return state


def _read_state_file(path: Path, *, allow_missing: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_text()
    except FileNotFoundError as exc:
        if allow_missing:
            return _empty_state()
        raise CoordinationUnavailable("coordination state file is missing") from exc
    except OSError as exc:
        raise CoordinationUnavailable(f"cannot read coordination state: {exc}") from exc
    try:
        state = json.loads(raw)
    except ValueError as exc:
        raise CoordinationUnavailable(
            f"coordination state does not parse: {exc}"
        ) from exc
    return _validate_state(state)


def _write_state_file(path: Path, state: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _owner_matches(
    record: dict[str, Any],
    machine_id: str,
    reservation_id: str,
    generation: int,
) -> bool:
    return (
        record.get("machine_id") == machine_id
        and record.get("reservation_id") == reservation_id
        and record.get("generation") == generation
    )


def _reservation_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": str(record.get("_phase", "")),
        "status": record.get("status"),
        "machine_id": record.get("machine_id"),
        "reservation_id": record.get("reservation_id"),
        "generation": record.get("generation"),
        "run_id": record.get("run_id"),
        "base_revisions": record.get("base_revisions"),
    }


__all__ = [
    "_empty_state",
    "_owner_matches",
    "_read_state_file",
    "_reservation_from_record",
    "_validate_state",
    "_write_state_file",
]
