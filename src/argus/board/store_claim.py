# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Claiming: one phase, a registered run, and the reserve mutation."""

import secrets
from collections.abc import Callable
from typing import Any

from argus.board.core import (
    ACTIVE_STATUSES,
    CoordinationError,
    _Mutation,
    _phase_key,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
    _validate_status,
)
from argus.board.records import _claim_one
from argus.board.state import _reservation_from_record
from argus.board.store_base import _StoreBase
from argus.board.worktree import _base_revisions, _transaction


class _ClaimOps(_StoreBase):
    def _reserve_mutation(
        self,
        phase: str | int,
        machine_id: str,
        reservation_id: str,
        generation: int,
        run_id: str | None,
        revisions: dict[str, Any],
        status: str,
    ) -> Callable[[dict[str, Any]], _Mutation]:
        key, number = _phase_key(phase)
        machine_id = _validate_machine_id(machine_id)
        _validate_status(status)
        if status not in ACTIVE_STATUSES:
            raise CoordinationError("a new reservation must start as active")

        def mutate(state: dict[str, Any]) -> _Mutation:
            record = _claim_one(
                state,
                key,
                machine_id,
                reservation_id,
                generation,
                run_id or f"phase-{key}",
                revisions,
                status,
                number,
            )
            return _Mutation(True, _reservation_from_record({**record, "_phase": key}))

        return mutate

    def reserve(
        self,
        phase: str | int,
        machine_id: str,
        *,
        reservation_id: str | None = None,
        generation: int | None = None,
        run_id: str | None = None,
        base_revisions: dict[str, Any] | None = None,
        status: str = "claimed",
    ) -> Any:
        key, number = _phase_key(phase)
        machine_id = _validate_machine_id(machine_id)
        reservation_id = reservation_id or f"rsv-{secrets.token_hex(12)}"
        _validate_reservation_id(reservation_id)
        generation = generation if generation is not None else 1
        _validate_generation(generation)
        revisions = base_revisions or _base_revisions(self.root, self.private)
        mutate = self._reserve_mutation(
            number,
            machine_id,
            reservation_id,
            generation,
            run_id or f"phase-{key}",
            revisions,
            status,
        )
        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: reserve {key} for {machine_id}",
        )

    def register_running(
        self,
        phase: str | int,
        machine_id: str,
        *,
        confirm: bool = False,
        run_id: str | None = None,
    ) -> Any:
        """Register a pre-coordination run as an active server claim."""
        if not confirm:
            raise CoordinationError("registering a running phase requires confirm=true")
        return self.reserve(phase, machine_id, run_id=run_id, status="running")


__all__ = ["_ClaimOps"]
