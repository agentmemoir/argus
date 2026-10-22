# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase fence every write path proves before its first write.

``--mode published`` has read the reservation before it wrote anything since the
fence existed. Every other writer that moves a checkout, cuts a branch, or
commits run state must read it too: a machine whose claim expired, was released,
or was taken over can otherwise move another owner's work, and the receipt it
leaves looks exactly like an owned one.

One helper and one result shape, so a new writer cannot invent a second fence
mechanism. Both states a caller may see are refusals: ``FENCED`` when the board
says this machine, reservation, or generation is not the owner, and
``COORDINATION_UNAVAILABLE`` when the reservation cannot be read or the board
cannot answer. Neither is ever a reason to write.

``clean`` asks a narrower question. It lands one phase's own run directory and
nothing else, so it only needs to know that no other machine is working there.
"""

import datetime as _datetime
from pathlib import Path
from typing import Any

from argus.gitsync.group_publication import _load_phase_reservation


def _phase_text(phase: object) -> str:
    """The phase as the run state names it, or as given when it is not a number."""
    try:
        return f"{int(str(phase)):06d}"
    except (TypeError, ValueError):
        return str(phase)


def _refusal(mode: str, phase: object, state: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mode": mode,
        "state": state,
        "error": error,
        "phase": _phase_text(phase),
    }


def _live_claim_elsewhere(store: Any, reservation: dict[str, Any]) -> str | None:
    """Why another machine still holds the phase's group, or ``None``.

    The group is the phase's own record and every record under the reservation
    id this phase was claimed with. A record that is terminal, released, or
    expired holds nothing. A record whose claim window cannot be read counts as
    live, because freeing a claim nobody can read could take it from its owner.
    """
    from argus.board.core import ACTIVE_STATUSES
    from argus.board.records import _claim_window_passed

    state, _commit = store.read()
    moment = _datetime.datetime.now(_datetime.UTC)
    for key, record in state["phases"].items():
        if key != reservation["phase"] and (
            record.get("reservation_id") != reservation.get("reservation_id")
        ):
            continue
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        if _claim_window_passed(record, moment):
            continue
        if record.get("machine_id") != reservation.get("machine_id"):
            return f"phase {key} is held by a live claim of another machine"
    return None


def phase_fence(root: str | Path, phase: int | str, mode: str) -> dict[str, Any] | None:
    """Return the refusal for a fence that does not hold, or ``None``.

    Reads the phase's own ``reservation.json`` and asks the board whether this
    machine, reservation id, and generation still own the phase. An expired or
    released record fails, and so does a terminal one: a gate that reads the
    fence must not run work the board already finished.

    A reservation that cannot be read is ``COORDINATION_UNAVAILABLE``, not a
    quiet pass. Reading nothing is not proof of ownership, and treating it as
    one would let a phase with no reservation at all write wherever it liked.

    ``clean`` passes when no other machine holds a live claim on the phase's
    group: a terminal, released, or expired record, and this machine's own live
    claim, all land. An escalated or abandoned phase is terminal or released,
    and refusing it would leave run state that traps the next publish.
    """
    root = Path(root).resolve()
    try:
        from argus.board.reservations import ReservationFenced, ReservationStore
    except Exception as exc:  # noqa: BLE001 - normalize boundary errors
        return _refusal(mode, phase, "COORDINATION_UNAVAILABLE", str(exc))
    try:
        reservation = _load_phase_reservation(root, int(str(phase)))
        store = ReservationStore(root)
        try:
            store.assert_owner(reservation)
        except ReservationFenced:
            if mode != "clean":
                raise
            if (held := _live_claim_elsewhere(store, reservation)) is not None:
                raise ReservationFenced(held) from None
    except ReservationFenced as exc:
        return _refusal(mode, phase, "FENCED", str(exc))
    except Exception as exc:  # noqa: BLE001 - fail closed, never write
        return _refusal(mode, phase, "COORDINATION_UNAVAILABLE", str(exc))
    return None


__all__ = ["phase_fence"]
