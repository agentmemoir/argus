# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Renewal, release, status changes, and the ownership fence."""

from argus.phase_reservations_core import (
    EXPIRED_STATUS,
    TERMINAL_STATUSES,
    CoordinationError,
    ReservationFenced,
    _as_moment,
    _iso,
    _Mutation,
    _now,
    _phase_key,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
    _validate_status,
)
from argus.phase_reservations_files import _validate_publication_receipt
from argus.phase_reservations_state import _owner_matches, _reservation_from_record
from argus.phase_reservations_store_base import _StoreBase
from argus.phase_reservations_worktree import _transaction


class _LifecycleOps(_StoreBase):
    def release_group(self, reservation, *, now=None):
        """Give one owner's whole group back, atomically, with no cooldown.

        The members of a group share one reservation id, so the release finds
        every member from that single token and removes them in one board write.
        A member already terminal (published) is left untouched: release never
        undoes published work. An explicit release writes no cooldown, so the
        next request may claim the group at once. Returns the released phase
        keys; a group with every member published releases nothing.

        Raises ``ReservationFenced`` when the token no longer owns an active
        member, including after an expiry, so a late release is rejected rather
        than reviving a group that already returned to the queue.
        """
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))
        moment = _iso(_as_moment(now))

        def mutate(state):
            keys = sorted(
                key
                for key, record in state["phases"].items()
                if record.get("reservation_id") == reservation_id
            )
            if not keys:
                raise ReservationFenced("reservation has no current members to release")
            released = []
            for key in keys:
                record = state["phases"][key]
                if not _owner_matches(record, machine_id, reservation_id, generation):
                    raise ReservationFenced(
                        f"phase {key} reservation is fenced by a newer owner"
                    )
                if record.get("status") == EXPIRED_STATUS:
                    raise ReservationFenced(f"phase {key} reservation expired")
                if record.get("status") in TERMINAL_STATUSES:
                    continue
                del state["phases"][key]
                released.append(key)
            return _Mutation(bool(released), released)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: release group "
            f"[{reservation_id}] for {machine_id} at {moment}",
        )

    def renew_group(self, reservation, status=None, *, now=None):
        """Renew every member of one group under its shared reservation id.

        One board write refreshes each active member's heartbeat; the group is
        renewed as a unit exactly as it was claimed. A published member is
        reported unchanged rather than refused, so a partial group can be
        renewed while its finished members stay completed. A member fenced by a
        newer owner, or one whose window already closed, raises
        ``ReservationFenced`` and renews nothing.
        """
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))
        if status is not None:
            status = _validate_status(status)
        moment = _iso(_as_moment(now))

        def mutate(state):
            keys = sorted(
                key
                for key, record in state["phases"].items()
                if record.get("reservation_id") == reservation_id
            )
            if not keys:
                raise ReservationFenced("reservation has no current members to renew")
            records = []
            refreshed = False
            for key in keys:
                record = state["phases"][key]
                if not _owner_matches(record, machine_id, reservation_id, generation):
                    raise ReservationFenced(
                        f"phase {key} reservation is fenced by a newer owner"
                    )
                if record.get("status") == EXPIRED_STATUS:
                    raise ReservationFenced(f"phase {key} reservation expired")
                if record.get("status") not in TERMINAL_STATUSES:
                    if status is not None:
                        record["status"] = status
                    record["heartbeat_at"] = moment
                    refreshed = True
                records.append(_reservation_from_record({**record, "_phase": key}))
            # A group whose every member is published has nothing to refresh, so
            # the tick that still holds it writes nothing rather than a board
            # commit per tick for a claim that is already finished.
            return _Mutation(refreshed, records)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: renew group [{reservation_id}] for {machine_id}",
        )

    def _owned_mutation(
        self,
        reservation,
        next_status=None,
        evidence_path=None,
        accepted_reason=None,
        now=None,
    ):
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))
        phase = reservation.get("phase")
        key, _number = _phase_key(phase)
        if next_status is not None:
            next_status = _validate_status(next_status)
        if next_status == "accepted":
            # An operator close-out is a decision, not a machine verdict, and
            # the next operator needs to know which it was. Refuse to record
            # the status without the justification.
            if not isinstance(accepted_reason, str) or not accepted_reason.strip():
                raise CoordinationError(
                    "accepted requires --accept-reason saying why the operator "
                    "closed the phase without a passing checker"
                )
            if len(accepted_reason) > 2000:
                raise CoordinationError(
                    "accepted reason is longer than 2000 characters"
                )
        elif accepted_reason is not None:
            raise CoordinationError(
                "an acceptance reason is only valid for accepted status"
            )
        receipt = None
        if next_status == "completed":
            if evidence_path is None:
                raise CoordinationError(
                    "completed requires a published completion receipt"
                )
            receipt = _validate_publication_receipt(
                evidence_path,
                reservation,
                self.private,
                self.remote,
                self.branch,
                repo_root=self.root,
            )
        elif evidence_path is not None:
            raise CoordinationError(
                "publication receipts are only valid for completed status"
            )

        def mutate(state):
            record = state["phases"].get(key)
            if record is None:
                raise ReservationFenced(f"phase {key} has no current reservation")
            if not _owner_matches(record, machine_id, reservation_id, generation):
                raise ReservationFenced(
                    f"phase {key} reservation is fenced by a newer owner"
                )
            if record.get("status") == EXPIRED_STATUS:
                # The window closed and the group returned to the queue; a late
                # write from the old owner must not revive it. This is the
                # generation fence applied to expiry, not a separate mechanism.
                raise ReservationFenced(f"phase {key} reservation expired")
            if record.get("status") in TERMINAL_STATUSES:
                if next_status == record.get("status"):
                    return _Mutation(
                        False, _reservation_from_record({**record, "_phase": key})
                    )
                raise ReservationFenced(
                    f"phase {key} is already {record.get('status')}"
                )
            if next_status is not None:
                record["status"] = next_status
            if next_status == "accepted":
                record["accepted_reason"] = accepted_reason.strip()
                record["accepted_at"] = _now()
            if receipt is not None:
                record["completion_receipt"] = receipt
            record["heartbeat_at"] = _iso(now)
            return _Mutation(True, _reservation_from_record({**record, "_phase": key}))

        return mutate

    def renew(self, reservation, status=None):
        mutate = self._owned_mutation(reservation, status)
        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: renew {reservation['phase']}",
        )

    def set_status(
        self, reservation, status, *, evidence_path=None, accepted_reason=None
    ):
        mutate = self._owned_mutation(
            reservation,
            status,
            evidence_path=evidence_path,
            accepted_reason=accepted_reason,
        )
        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: {status} {reservation['phase']}",
        )

    def assert_owner(self, reservation):
        """Fetch the latest board and verify the three-part fence."""
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        key, _number = _phase_key(reservation.get("phase"))
        state, _commit = self.read()
        record = state["phases"].get(key)
        if record is None or not _owner_matches(
            record,
            reservation.get("machine_id"),
            reservation.get("reservation_id"),
            reservation.get("generation"),
        ):
            raise ReservationFenced(
                f"phase {key} is no longer owned by this reservation"
            )
        if record.get("status") in TERMINAL_STATUSES:
            return dict(record)
        return dict(record)
