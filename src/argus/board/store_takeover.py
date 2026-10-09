# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Operator takeover of a whole group under a new reservation."""

import secrets
from typing import Any

from argus.board.core import (
    TERMINAL_STATUSES,
    CoordinationError,
    PublicationBusy,
    ReservationConflict,
    ReservationFenced,
    _Mutation,
    _now,
    _phase_key,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
)
from argus.board.state import _reservation_from_record
from argus.board.store_base import _StoreBase
from argus.board.worktree import _base_revisions, _transaction


class _TakeoverOps(_StoreBase):
    def takeover(
        self,
        phase: str | int,
        machine_id: str,
        *,
        confirm: bool = False,
        run_id: str | None = None,
        base_revisions: dict[str, Any] | None = None,
        expected_reservation_id: Any = None,
        expected_generation: Any = None,
    ) -> Any:
        """Take one claim over under a new reservation id, for a whole group.

        A reservation id covers a whole group, so the takeover moves every member
        that shares it: one new reservation id and one new generation for all of
        them, and the previous owner is fenced for every member at once. A member
        that is already finished keeps its record. The named phase is the entry
        point of the takeover and the record this returns.
        """
        if not confirm:
            raise CoordinationError("explicit operator takeover requires confirm=true")
        if expected_reservation_id is None or expected_generation is None:
            raise CoordinationError(
                "takeover requires the expected old reservation ID and generation"
            )
        key, number = _phase_key(phase)
        machine_id = _validate_machine_id(machine_id)
        new_id = f"rsv-{secrets.token_hex(12)}"
        revisions = base_revisions or _base_revisions(self.root, self.private)
        current_state, _current_commit = self.read()
        current: dict[str, Any] | None = current_state["phases"].get(key)
        if current is None:
            raise ReservationConflict(number, reason="phase has no claim")
        if expected_reservation_id is not None:
            expected_reservation_id = _validate_reservation_id(expected_reservation_id)
        if expected_generation is not None:
            expected_generation = _validate_generation(expected_generation)
        if (
            expected_reservation_id is not None
            and current.get("reservation_id") != expected_reservation_id
        ) or (
            expected_generation is not None
            and current.get("generation") != expected_generation
        ):
            raise ReservationFenced(
                f"phase {key} changed; inspect the current owner before retrying"
            )
        expected_generation = _validate_generation(current.get("generation"))
        expected_reservation_id = current.get("reservation_id")
        # One reservation id covers a whole group, so the group is taken over as
        # a unit. The named phase is the entry point; every member under the same
        # id moves with it. Rewriting the named one alone would give the group two
        # reservation ids, and release, renewal and expiry all move a group by its
        # shared id, so the members left behind would hold a claim nobody renews
        # and a release nobody returns.
        members = sorted(
            member
            for member, record in current_state["phases"].items()
            if record.get("reservation_id") == expected_reservation_id
        )

        def mutate(state: dict[str, Any]) -> _Mutation:
            old = state["phases"].get(key)
            if old is None:
                raise ReservationConflict(number, reason="phase has no claim")
            if (
                old.get("machine_id") == machine_id
                and old.get("reservation_id") == new_id
            ):
                return _Mutation(
                    False, _reservation_from_record({**old, "_phase": key})
                )
            if (
                old.get("generation") != expected_generation
                or old.get("reservation_id") != expected_reservation_id
            ):
                raise ReservationFenced(
                    f"phase {key} changed before takeover could commit"
                )
            publication = state.get("publication")
            if publication is not None:
                raise PublicationBusy(
                    "cannot take over a phase while its publication lock is held"
                )
            if old.get("status") == "completed":
                raise ReservationConflict(
                    number, old, "completed reservations cannot be taken over"
                )
            result = None
            for member in members:
                record = state["phases"].get(member)
                if record is None:
                    raise ReservationFenced(
                        f"phase {member} changed before takeover could commit"
                    )
                if (
                    record.get("machine_id") != old.get("machine_id")
                    or record.get("generation") != expected_generation
                ):
                    raise ReservationFenced(
                        f"phase {member} changed before takeover could commit"
                    )
                if member != key and record.get("status") in TERMINAL_STATUSES:
                    # A member that is already finished keeps its record, so a
                    # takeover never undoes published work. The phase the caller
                    # named is the exception: naming it is the decision, and the
                    # check above refuses a completed one before this point.
                    continue
                new = {
                    "status": "claimed",
                    "machine_id": machine_id,
                    "reservation_id": new_id,
                    "generation": expected_generation + 1,
                    "claimed_at": _now(),
                    "heartbeat_at": _now(),
                    "expires_at": None,
                    # The member's own run id where it has one, so a group keeps
                    # each member's run directory rather than renaming them all to
                    # the phase the takeover was asked about. A run id the caller
                    # named applies to the phase it named.
                    "run_id": (
                        run_id
                        if member == key and run_id
                        else record.get("run_id") or f"phase-{member}"
                    ),
                    "base_revisions": revisions,
                }
                state["phases"][member] = new
                if member == key:
                    result = _reservation_from_record({**new, "_phase": key})
            if result is None:
                raise ReservationConflict(
                    number, old, "completed reservations cannot be taken over"
                )
            return _Mutation(True, result)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: explicit takeover "
            f"[{','.join(members)}] by {machine_id}",
        )


__all__ = ["_TakeoverOps"]
