# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The short publication lock: acquire and release."""

import datetime as _datetime
from typing import Any

from argus.board.core import (
    TERMINAL_STATUSES,
    PublicationBusy,
    ReservationFenced,
    _Mutation,
    _now,
    _phase_key,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
)
from argus.board.records import _publication_lock_stale
from argus.board.state import _owner_matches
from argus.board.store_base import _StoreBase
from argus.board.worktree import _transaction


class _PublicationOps(_StoreBase):
    def acquire_publication(
        self,
        reservation: dict[str, Any],
        *,
        now: _datetime.datetime | str | None = None,
    ) -> Any:
        key, _number = _phase_key(reservation.get("phase"))
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))

        def mutate(state: dict[str, Any]) -> _Mutation:
            record = state["phases"].get(key)
            if record is None or not _owner_matches(
                record, machine_id, reservation_id, generation
            ):
                raise ReservationFenced(
                    f"phase {key} is no longer owned by this reservation"
                )
            if record.get("status") in TERMINAL_STATUSES:
                raise ReservationFenced(
                    f"phase {key} is already {record.get('status')}"
                )
            current = state.get("publication")
            wanted = {
                "phase": key,
                "machine_id": machine_id,
                "reservation_id": reservation_id,
                "generation": generation,
                "acquired_at": _now(),
            }
            if current is not None and not _publication_lock_stale(current, now):
                same = (
                    current.get("phase") == key
                    and current.get("machine_id") == machine_id
                    and current.get("reservation_id") == reservation_id
                    and current.get("generation") == generation
                )
                if not same:
                    raise PublicationBusy(
                        f"publication lock is held by phase {current.get('phase', '?')}"
                    )
                return _Mutation(False, current)
            state["publication"] = wanted
            return _Mutation(True, wanted)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: publication lock {key}",
        )

    def release_publication(self, reservation: dict[str, Any]) -> Any:
        key, _number = _phase_key(reservation.get("phase"))
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))

        def mutate(state: dict[str, Any]) -> _Mutation:
            current = state.get("publication")
            if current is None:
                return _Mutation(False, None)
            same = (
                current.get("phase") == key
                and current.get("machine_id") == machine_id
                and current.get("reservation_id") == reservation_id
                and current.get("generation") == generation
            )
            if not same:
                raise ReservationFenced("publication lock belongs to another phase")
            del state["publication"]
            return _Mutation(True, None)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: release publication lock {key}",
        )


__all__ = ["_PublicationOps"]
