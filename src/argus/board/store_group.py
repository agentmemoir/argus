# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Group claims and the stale-claim sweep."""

import datetime as _datetime
import secrets
from collections.abc import Sequence
from typing import Any

from argus.board.core import (
    ACTIVE_STATUSES,
    CoordinationError,
    _as_moment,
    _Mutation,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
    _validate_status,
)
from argus.board.records import _claim_one, _expire_records
from argus.board.store_base import _StoreBase
from argus.board.worktree import _base_revisions, _transaction


class _GroupOps(_StoreBase):
    def reserve_group(
        self,
        phases: Sequence[str | int],
        machine_id: str,
        *,
        reservation_id: str | None = None,
        generation: int | None = None,
        run_ids: dict[str, str] | None = None,
        base_revisions: dict[str, Any] | None = None,
        status: str = "claimed",
        now: _datetime.datetime | str | None = None,
    ) -> Any:
        """Claim every phase in ``phases`` in one board write, or claim none.

        The members share one reservation id, so the driver fences the whole
        group from a single token. ``_transaction`` runs the whole mutation
        before it writes, commits, and pushes, so a conflict on any member
        raises and the board is left untouched: half a group is never
        reserved. A member already held by this same reservation id is
        refreshed rather than refused, which makes a retried request
        idempotent.

        A request makes two board writes, not one: the expiry sweep, then this
        claim. They are deliberately separate, because the sweep has to land
        even when the claim refuses. A request that arrives after a window has
        closed must leave the stale claim expired and then fail on the cooldown;
        rolling the sweep back with the refused claim would leave the group
        reading as live and report a conflict instead of the cooldown the
        request rules promise.

        Returns one reservation dict per member, in the order given.
        """
        if self.issues:
            raise CoordinationError("issue reservations are claimed one at a time")
        keys: list[str] = []
        for phase in phases:
            key, _number = self._key(phase)
            if key in keys:
                raise CoordinationError(
                    f"{self.kind} {key} appears twice in one group claim"
                )
            keys.append(key)
        if not keys:
            raise CoordinationError("a group claim needs at least one phase")
        machine_id = _validate_machine_id(machine_id)
        reservation_id = reservation_id or f"rsv-{secrets.token_hex(12)}"
        _validate_reservation_id(reservation_id)
        generation = generation if generation is not None else 1
        _validate_generation(generation)
        _validate_status(status)
        if status not in ACTIVE_STATUSES:
            raise CoordinationError("a new reservation must start as active")
        moment = _as_moment(now)
        # Sweep first so a request that arrives after a window closed returns the
        # stale group to the queue and then fails closed on the cooldown, rather
        # than reading a dead claim as live. The sweep is its own bookkeeping
        # write; the claim below is a separate one.
        self.expire_stale(now=moment)
        revisions = base_revisions or _base_revisions(self.root, self.private)
        run_ids = run_ids or {}

        def mutate(state: dict[str, Any]) -> _Mutation:
            records: list[Any] = []
            for key in keys:
                number = int(key)
                record = _claim_one(
                    state,
                    key,
                    machine_id,
                    reservation_id,
                    generation,
                    run_ids.get(key),
                    revisions,
                    status,
                    number,
                    moment,
                )
                records.append(self._reservation(record, key))
            return _Mutation(True, records)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"{self.kind} coordination: reserve group [{','.join(keys)}] for {machine_id}",
            issues=self.issues,
        )

    def expire_stale(self, now: _datetime.datetime | str | None = None) -> Any:
        """Return every stale group to the queue under a cooldown stamp.

        A claim is stale when no work started inside the start window, or, once
        work has begun, when its newest heartbeat is older than the heartbeat
        window. Every member of one reservation moves in a single board write,
        so a group is never half expired. The window is measured from an
        injected clock when a test passes one. Returns the expired members as
        ``{"phase", "machine_id", "reservation_id"}`` dicts; an empty list means
        nothing was stale and no write was made.
        """
        moment = _as_moment(now) or _datetime.datetime.now(_datetime.UTC)

        def mutate(state: dict[str, Any]) -> _Mutation:
            expired = _expire_records(state, moment, issues=self.issues)
            return _Mutation(bool(expired), expired)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"{self.kind} coordination: expire stale claims",
            issues=self.issues,
        )


__all__ = ["_GroupOps"]
