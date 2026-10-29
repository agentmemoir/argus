# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Fenced issue write-back and escalation records on the shared board."""

import json
from typing import Any, cast

from argus.board.core import (
    EXPIRED_STATUS,
    TERMINAL_STATUSES,
    CoordinationError,
    ReservationFenced,
    _Mutation,
    _now,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
)
from argus.board.state import _owner_matches, _receipt_of
from argus.board.store_base import _StoreBase
from argus.board.worktree import _transaction


class _IssueOps(_StoreBase):
    def set_issue_receipt(self, reservation: Any, receipt: Any) -> dict[str, Any]:
        """Keep retry or escalation evidence under the claim's generation fence.

        ``None`` clears a successfully posted write-back. An expired escalation
        record can only be changed to a cleared escalation by its matching token.
        """
        if not self.issues:
            raise CoordinationError("issue receipts require the issue board")
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        reservation = cast("dict[str, Any]", reservation)
        key, _number = self._key(reservation.get("issue"))
        machine = _validate_machine_id(reservation.get("machine_id"))
        token = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))
        if receipt is not None:
            if not isinstance(receipt, dict):
                raise CoordinationError("issue receipt must be an object")
            receipt = cast("dict[str, Any]", receipt)
            if receipt.get("state_reason") not in {
                "writeback_pending",
                "remedy_escalated",
            }:
                raise CoordinationError("issue receipt has an invalid state_reason")
            if receipt.get("state_reason") == "remedy_escalated" and not isinstance(
                receipt.get("cleared"), bool
            ):
                raise CoordinationError(
                    "issue escalation receipt needs cleared=true or false"
                )
            try:
                receipt = json.loads(json.dumps(receipt))
            except (ValueError, TypeError) as exc:
                raise CoordinationError(
                    "issue receipt must contain JSON values"
                ) from exc

        def mutate(state: dict[str, Any]) -> _Mutation:
            record = state["phases"].get(key)
            if record is None or not _owner_matches(record, machine, token, generation):
                raise ReservationFenced(
                    f"issue {key} is no longer owned by this reservation"
                )
            if record.get("status") in TERMINAL_STATUSES:
                raise ReservationFenced(f"issue {key} is already {record['status']}")
            escalated = _receipt_of(record).get("state_reason") == "remedy_escalated"
            if receipt is not None and receipt.get("cleared") is True and not escalated:
                raise CoordinationError("issue has no escalation receipt to clear")
            if record.get("status") == EXPIRED_STATUS and not (
                escalated
                and receipt is not None
                and receipt.get("state_reason") == "remedy_escalated"
                and receipt.get("cleared") is True
            ):
                raise ReservationFenced(f"issue {key} reservation expired")
            if receipt is None:
                record.pop("completion_receipt", None)
            else:
                record["completion_receipt"] = receipt
            record["heartbeat_at"] = _now()
            return _Mutation(True, self._reservation(record, key))

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"issue coordination: record receipt for {key}",
            issues=True,
        )

    def set_issue_pull_request(
        self, reservation: Any, pull_request: dict[str, Any]
    ) -> dict[str, Any]:
        """Record the branch and pull request URL that an Argus issue run owns.

        The reservation must own the record, as a receipt write does. The write
        does not renew the claim, so the record is not a lease.
        """
        if not self.issues:
            raise CoordinationError("issue pull requests require the issue board")
        if not isinstance(reservation, dict):
            raise CoordinationError("reservation must be an object")
        reservation = cast("dict[str, Any]", reservation)
        key, _number = self._key(reservation.get("issue"))
        machine = _validate_machine_id(reservation.get("machine_id"))
        token = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))
        branch = pull_request.get("branch")
        url = pull_request.get("url")
        if (
            not isinstance(branch, str)
            or not isinstance(url, str)
            or not branch
            or not url.startswith("https://")
            or len(url) > 500
            or any(char.isspace() for char in branch + url)
        ):
            raise CoordinationError(
                "issue pull request needs a branch and an https URL"
            )
        value = {"branch": branch, "url": url}

        def mutate(state: dict[str, Any]) -> _Mutation:
            record = state["phases"].get(key)
            if record is None or not _owner_matches(record, machine, token, generation):
                raise ReservationFenced(
                    f"issue {key} is no longer owned by this reservation"
                )
            record["pull_request"] = value
            return _Mutation(True, self._reservation(record, key))

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"issue coordination: record pull request for {key}",
            issues=True,
        )


__all__ = ["_IssueOps"]
