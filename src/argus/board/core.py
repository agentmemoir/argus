# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared primitives: statuses, errors, clock stamps, and input validation."""

import datetime as _datetime
import json
import re
from typing import Any

#: A branch name used only in isolated self-tests.
SELF_TEST_BRANCH = "fixture-claims"


#: The board's own branch. It is never checked out, and nothing else commits to
#: it. It used to be the integration branch, which put a board commit and a
#: phase's own run-state commit in the same place: every reservation moved the
#: branch the phase's commits were based on, so publishing a finished phase
#: could be rejected as non-fast-forward by a bookkeeping write.
#: Where run state and phase notes are published, used only when the remote
#: cannot say which branch it considers its default.
INTEGRATION_FALLBACK = "master"


COORDINATION_VERSION = 1


STATUSES = frozenset(
    {
        "claimed",
        "running",
        "paused",
        "blocked",
        "completed",
        "rejected",
        "accepted",
        "expired",
    }
)


ACTIVE_STATUSES = frozenset({"claimed", "running", "paused", "blocked"})


#: A claim a window closed on. It is neither active nor terminal: the old owner
#: is fenced, and after the cooldown the group can be claimed again.
EXPIRED_STATUS = "expired"


# "completed" means the pipeline's finalize step published the phase.
# "accepted" means an operator closed it deliberately, without a passing
# checker, and recorded that decision in the phase file. It is terminal for
# the same reason, but it must never require a publication receipt: finalize
# did not run, so no receipt with real commit SHAs exists to point at.
TERMINAL_STATUSES = frozenset({"completed", "rejected", "accepted"})


MAX_TRANSACTION_ATTEMPTS = 4


GIT_TIMEOUT_S = 60


FETCH_TIMEOUT_S = 180


#: Prefix every disposable coordination checkout is created under, and the only
#: prefix the sweep below is allowed to delete.
WORKTREE_PREFIX = "clio-phase-coordination-"


#: How old a leftover checkout must be before the sweep removes it. Two runs on
#: one machine must never delete each other's, so the age has to exceed the gap
#: between transactions by a wide margin. A driver tick is ten minutes; an hour
#: is six of them.
WORKTREE_SWEEP_AGE_S = 3600


MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


RESERVATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


PHASE_KEY_RE = re.compile(r"^[0-9]{6}$")
ISSUE_KEY_RE = re.compile(r"^[0-9]{1,10}$")


PUBLICATION_NAME = "publication.json"


#: The board field an expired claim carries while it waits out the cooldown
#: before another agent may take it. Stage 2 reserves the field and the
#: refusal path; the lifecycle that writes it lands in a later stage. A record
#: with no field is claimable as it always was, so an old board still reads.
COOLDOWN_UNTIL_FIELD = "cooldown_until"


#: Before any work starts, a claimed group expires once it has sat this long
#: without being renewed. The value is provisional and has not been measured
#: against real queue behaviour, so it lives here as one named constant.
GROUP_START_WINDOW_S = 48 * 3600


#: After work begins, a group expires when its newest heartbeat is older than
#: this. Expiry keys off the heartbeat rather than a fixed deadline, so a long
#: run is not cut off while its driver keeps renewing. Provisional.
GROUP_HEARTBEAT_WINDOW_S = 30 * 60


#: After an expiry, a group waits this long before another agent may claim it.
#: The window lets a stalled but still-live owner notice it lost the claim and
#: stop. An explicit release has no cooldown. Provisional.
GROUP_COOLDOWN_S = 60


class CoordinationError(Exception):
    """Base class for errors that must prevent unclaimed work."""


class CoordinationUnavailable(CoordinationError):
    """The coordination branch could not be fetched, read, or pushed."""


class ReservationConflict(CoordinationError):
    """Another owner, or another reservation, already owns the phase."""

    def __init__(
        self,
        phase: str | int,
        record: dict[str, Any] | None = None,
        reason: str = "phase is already reserved",
        *,
        kind: str = "phase",
    ) -> None:
        self.phase = phase
        self.record = record or {}
        self.reason = reason
        owner = self.record.get("machine_id", "unknown")
        status = self.record.get("status", "unknown")
        super().__init__(
            f"{kind} {phase} is unavailable ({self.reason}; "
            f"owner={owner}, status={status})"
        )


class ReservationFenced(CoordinationError):
    """The caller's machine, reservation, or generation is stale."""


class ReservationCooldown(CoordinationError):
    """An expired claim is still in its cooldown window.

    A released or expired group waits a short window before another agent may
    claim it, so an agent that is still alive but stalled has time to notice it
    lost the reservation and stop. A request during that window fails closed
    and reports the time left; a request never shortens the window.
    """

    def __init__(
        self,
        phase: str | int,
        remaining_seconds: int | float,
        record: dict[str, Any] | None = None,
        *,
        kind: str = "phase",
    ) -> None:
        self.phase = phase
        self.remaining_seconds = int(remaining_seconds)
        self.record = record or {}
        super().__init__(
            f"{kind} {phase} is in cooldown; {self.remaining_seconds} second(s) left"
        )


class PublicationBusy(CoordinationError):
    """Another phase currently owns the short publication lock."""


class _Mutation:
    def __init__(self, changed: bool, value: Any) -> None:
        self.changed = changed
        self.value = value


def _iso(moment: _datetime.datetime | None = None) -> str:
    """Format a moment (or now) as the board's Zulu millisecond stamp."""
    moment = moment or _datetime.datetime.now(_datetime.UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_datetime.UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_moment(
    value: _datetime.datetime | str | None,
) -> _datetime.datetime | None:
    """Normalize an optional injected clock to an aware UTC datetime.

    ``None`` means the caller did not inject a clock, and the caller then reads
    the real one where it needs it. A string is parsed the same way the board
    writes stamps, so a test can pass either a datetime or a stamp.
    """
    if value is None:
        return None
    if isinstance(value, _datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_datetime.UTC)
        return value
    return _parse_moment(value, what="time")


def _parse_moment(value: object, *, what: str = "timestamp") -> _datetime.datetime:
    """Parse a board stamp; a value nobody can parse fails closed."""
    if not isinstance(value, str) or not value:
        raise CoordinationUnavailable(f"invalid {what}: {value!r}")
    try:
        moment = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoordinationUnavailable(f"invalid {what}: {value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_datetime.UTC)
    return moment


def _now() -> str:
    return _iso()


def _issue_key(value: object) -> tuple[str, int]:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CoordinationError(f"invalid issue number: {value!r}")
    if isinstance(value, str) and not ISSUE_KEY_RE.fullmatch(value):
        raise CoordinationError(f"invalid issue number: {value!r}")
    number = int(value)
    if not 1 <= number <= 9999999999:
        raise CoordinationError(f"issue number is out of range: {value!r}")
    return f"{number:06d}", number


def _phase_key(phase: Any) -> tuple[str, int]:
    try:
        if isinstance(phase, bool):
            raise ValueError
        number = int(phase)
    except (TypeError, ValueError):
        raise CoordinationError(f"invalid phase number: {phase!r}") from None
    if number < 0 or number > 999999:
        raise CoordinationError(f"phase number out of range: {number}")
    return f"{number:06d}", number


def _validate_machine_id(machine_id: object) -> str:
    if not isinstance(machine_id, str) or not MACHINE_ID_RE.fullmatch(machine_id):
        raise CoordinationError(
            "machine_id must contain only letters, numbers, '.', '_' or '-' "
            "and be at most 128 characters"
        )
    return machine_id


def _validate_branch(branch: object) -> str:
    if (
        not isinstance(branch, str)
        or not BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith("/")
    ):
        raise CoordinationError(f"invalid coordination branch: {branch!r}")
    return branch


def _validate_remote(remote: object) -> str:
    if not isinstance(remote, str) or not REMOTE_RE.fullmatch(remote):
        raise CoordinationError(f"invalid Git remote: {remote!r}")
    return remote


def _validate_reservation_id(reservation_id: object) -> str:
    if not isinstance(reservation_id, str) or not RESERVATION_ID_RE.fullmatch(
        reservation_id
    ):
        raise CoordinationError(
            "reservation_id must contain only letters, numbers, '.', '_' or "
            "'-' and be at most 128 characters"
        )
    return reservation_id


def _validate_generation(generation: object) -> int:
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise CoordinationError("generation must be a positive integer")
    return generation


def _validate_status(status: str) -> str:
    if status not in STATUSES:
        raise CoordinationError(f"invalid reservation status: {status!r}")
    return status


def _safe_detail(text: str | None, limit: int = 500) -> str:
    """Keep diagnostics useful without echoing a credential-bearing URL."""
    text = (text or "").replace("\n", " ").strip()
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", text)
    return text[:limit]


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


__all__ = [
    "_Mutation",
    "_as_moment",
    "_iso",
    "_issue_key",
    "_now",
    "_parse_moment",
    "_phase_key",
    "_print_json",
    "_safe_detail",
    "_validate_branch",
    "_validate_generation",
    "_validate_machine_id",
    "_validate_remote",
    "_validate_reservation_id",
    "_validate_status",
]
