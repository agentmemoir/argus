# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Claim windows: cooldown, expiry, and the single claim writer."""

import datetime as _datetime
import math
from typing import Any

from argus.board.core import (
    ACTIVE_STATUSES,
    COOLDOWN_UNTIL_FIELD,
    EXPIRED_STATUS,
    GROUP_COOLDOWN_S,
    GROUP_HEARTBEAT_WINDOW_S,
    GROUP_START_WINDOW_S,
    TERMINAL_STATUSES,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationCooldown,
    _as_moment,
    _iso,
    _parse_moment,
)
from argus.board.state import _owner_matches, _receipt_of


def _cooldown_remaining(
    record: dict[str, Any],
    _number: int,
    now: _datetime.datetime | str | None = None,
) -> int:
    """Seconds left in a record's claim cooldown, or 0 when none is active.

    Stage 3 writes ``COOLDOWN_UNTIL_FIELD`` when an expired group returns to
    the queue. This stage reserves the field and the refusal path: a record
    that carries a future timestamp refuses a claim, and a record without the
    field is claimable as it always was, so an old board still reads. A
    malformed timestamp fails closed rather than reading as expired, because a
    board nobody can parse is not a board that permits a claim.
    """
    value = record.get(COOLDOWN_UNTIL_FIELD)
    if value is None:
        return 0
    until = _parse_moment(value, what=COOLDOWN_UNTIL_FIELD)
    moment = _as_moment(now) or _datetime.datetime.now(_datetime.UTC)
    delta = (until - moment).total_seconds()
    if delta <= 0:
        return 0
    return math.ceil(delta)


def _claim_window(record: dict[str, Any]) -> tuple[_datetime.datetime, int] | None:
    """The moment and length a claim is measured against, or ``None``.

    Before any work starts the claim is measured from ``claimed_at`` against the
    start window; once work has begun it is measured from ``heartbeat_at``
    against the heartbeat window. A record whose stamp is missing or malformed
    returns ``None`` and is left alone, because expiring a possibly-live claim on
    a stamp nobody can read would take work away from its owner.
    """
    status = record.get("status")
    if status == "claimed":
        field, window = "claimed_at", GROUP_START_WINDOW_S
    elif status in ACTIVE_STATUSES:
        field, window = "heartbeat_at", GROUP_HEARTBEAT_WINDOW_S
    else:
        return None
    try:
        reference = _parse_moment(record.get(field), what=field)
    except CoordinationUnavailable:
        return None
    return reference, window


def _publication_lock_stale(
    lock: dict[str, Any], now: _datetime.datetime | str | None = None
) -> bool:
    """Whether the publication lock has outlived the longest a live run stays silent.

    One publish takes the lock and ends by releasing it. A publish that dies
    before the release leaves the lock for good, and every takeover and every
    other phase's publish then refuses it. The lock is stale once it is older
    than the heartbeat window, the time after which a silent owner loses its
    claim. A stamp that is missing or unreadable is not stale, because freeing a
    lock nobody can read could take it from a live publish.
    """
    try:
        acquired = _parse_moment(lock.get("acquired_at"), what="acquired_at")
    except CoordinationUnavailable:
        return False
    moment = _as_moment(now) or _datetime.datetime.now(_datetime.UTC)
    return (moment - acquired).total_seconds() > GROUP_HEARTBEAT_WINDOW_S


def _claim_window_passed(record: dict[str, Any], moment: _datetime.datetime) -> bool:
    measured = _claim_window(record)
    if measured is None:
        return False
    reference, window = measured
    return (moment - reference).total_seconds() > window


def _expire_records(
    state: dict[str, Any], moment: _datetime.datetime, *, issues: bool = False
) -> list[dict[str, Any]]:
    """Move every stale group to ``EXPIRED_STATUS`` under one cooldown stamp.

    The members of one reservation share a reservation id, so the group is
    expired as a unit: one member past its window expires all of them, and the
    old owner is fenced for every member at once. Returns the expired records,
    each with its phase key, so the caller can report what moved.
    """
    groups: dict[tuple[Any, Any], list[tuple[Any, Any]]] = {}
    for key, record in state["phases"].items():
        if issues and _receipt_of(record).get("state_reason") == "writeback_pending":
            continue
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        reservation_id = record.get("reservation_id")
        if not reservation_id:
            continue
        groups.setdefault((record.get("machine_id"), reservation_id), []).append(
            (key, record)
        )
    expired: list[dict[str, Any]] = []
    for members in groups.values():
        if not any(_claim_window_passed(record, moment) for _key, record in members):
            continue
        until = _iso(moment + _datetime.timedelta(seconds=GROUP_COOLDOWN_S))
        for key, record in members:
            record["status"] = EXPIRED_STATUS
            record[COOLDOWN_UNTIL_FIELD] = until
            expired.append(
                {
                    "issue" if issues else "phase": int(key) if issues else key,
                    "machine_id": record.get("machine_id"),
                    "reservation_id": record.get("reservation_id"),
                }
            )
    return expired


def _claim_one(
    state: dict[str, Any],
    key: str,
    machine_id: str,
    reservation_id: str,
    generation: int,
    run_id: str | None,
    revisions: dict[str, Any],
    status: str,
    number: int,
    now: _datetime.datetime | None = None,
    *,
    issues: bool = False,
) -> dict[str, Any]:
    """Write or refresh one claim in ``state``; return its phase record.

    One helper backs both ``reserve`` and ``reserve_group`` so a single claim
    and a group claim cannot drift apart about who may claim what. A terminal
    record, another owner, and an active cooldown all raise. ``_transaction``
    runs the whole mutation before it writes anything, so a raise leaves the
    board untouched: a group is claimed atomically or not at all, and half a
    group is never reserved.
    """
    existing: dict[str, Any] | None = state["phases"].get(key)
    kind = "issue" if issues else "phase"
    if existing is not None:
        receipt = _receipt_of(existing)
        if (
            issues
            and receipt.get("state_reason") == "remedy_escalated"
            and receipt.get("cleared") is not True
        ):
            raise ReservationConflict(
                number, existing, "remedy escalation needs clearance", kind=kind
            )
        if existing.get("status") == EXPIRED_STATUS:
            remaining = _cooldown_remaining(existing, number, now)
            if remaining > 0:
                raise ReservationCooldown(number, remaining, existing, kind=kind)
            # The cooldown has passed, so the expired record is claimable again
            # and is overwritten below with a fresh reservation.
        elif existing.get("status") in TERMINAL_STATUSES:
            raise ReservationConflict(
                number,
                existing,
                f"{kind} is retained as {existing.get('status')}",
                kind=kind,
            )
        elif _owner_matches(existing, machine_id, reservation_id, generation):
            existing["status"] = status
            existing["heartbeat_at"] = _iso(now)
            return existing
        else:
            remaining = _cooldown_remaining(existing, number, now)
            if remaining > 0:
                raise ReservationCooldown(number, remaining, existing, kind=kind)
            raise ReservationConflict(
                number, existing, f"{kind} is already reserved", kind=kind
            )
    record = {
        "status": status,
        "machine_id": machine_id,
        "reservation_id": reservation_id,
        "generation": generation,
        "claimed_at": _iso(now),
        "heartbeat_at": _iso(now),
        # Expiry keys off the start and heartbeat windows, not this field, which
        # is kept for audit and tool compatibility.
        "expires_at": None,
        "run_id": run_id or f"{kind}-{key}",
        "base_revisions": revisions,
    }
    state["phases"][key] = record
    return record


__all__ = ["_claim_one", "_expire_records", "_publication_lock_stale"]
