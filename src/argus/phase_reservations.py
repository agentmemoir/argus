#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared, fenced phase reservations backed by a configured Git board.

The board checkout, file, branch and remote come from the loaded config. The
board branch holds no phase work; it is never checked out as a phase branch.
Every write uses a short-lived detached Git worktree and a normal, non-forced
push. A rejected push is retried from a fresh fetch, so one client can win a
claim without overwriting another.

The board had its own branch because it used to share the integration branch
with run state and phase notes.  A reservation, a heartbeat and a status change
were each a commit there, so the branch a phase's commits were based on moved
every time the board changed, and publishing a finished phase could be rejected
as non-fast-forward by a bookkeeping write.  The first transaction after the
move seeds the branch from the integration branch's tip, which carries the
existing state file across, so no live reservation is lost.

A reservation is an ownership record, not completion evidence. The pipeline
must publish the current phase and a matching ``publication.json`` receipt
before the record can change to ``completed``.

The module is intentionally usable both as a library and as a small CLI.
The CLI is useful for registering a phase that was already running before
coordination was enabled, and for explicit operator takeover when a process
has stopped without releasing its claim.
"""

import argparse
import contextlib
import datetime as _datetime
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


if __package__ in {None, ""}:
    import _bootstrap  # noqa: F401 - register the source package
from argus.config import ConfigError
from argus import conventions
from argus.conventions import board_repo, phase_run_dir
from argus.exec_tempdir import make_exec_dir


def state_rel():
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


def state_path(repo):
    root = Path(repo).resolve()
    path = root / state_rel()
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise CoordinationError(
            "coordination file must stay inside its repository"
        ) from exc
    return path


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

    def __init__(self, phase, record=None, reason="phase is already reserved"):
        self.phase = phase
        self.record = record or {}
        self.reason = reason
        owner = self.record.get("machine_id", "unknown")
        status = self.record.get("status", "unknown")
        super().__init__(
            f"phase {phase} is unavailable ({self.reason}; "
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

    def __init__(self, phase, remaining_seconds, record=None):
        self.phase = phase
        self.remaining_seconds = int(remaining_seconds)
        self.record = record or {}
        super().__init__(
            f"phase {phase} is in cooldown; {self.remaining_seconds} second(s) left"
        )


class PublicationBusy(CoordinationError):
    """Another phase currently owns the short publication lock."""


class _Mutation:
    def __init__(self, changed, value):
        self.changed = changed
        self.value = value


def _iso(moment=None):
    """Format a moment (or now) as the board's Zulu millisecond stamp."""
    moment = moment or _datetime.datetime.now(_datetime.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_datetime.timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_moment(value):
    """Normalize an optional injected clock to an aware UTC datetime.

    ``None`` means the caller did not inject a clock, and the caller then reads
    the real one where it needs it. A string is parsed the same way the board
    writes stamps, so a test can pass either a datetime or a stamp.
    """
    if value is None:
        return None
    if isinstance(value, _datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_datetime.timezone.utc)
        return value
    return _parse_moment(value, what="time")


def _parse_moment(value, *, what="timestamp"):
    """Parse a board stamp; a value nobody can parse fails closed."""
    if not isinstance(value, str) or not value:
        raise CoordinationUnavailable(f"invalid {what}: {value!r}")
    try:
        moment = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoordinationUnavailable(f"invalid {what}: {value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_datetime.timezone.utc)
    return moment


def _now():
    return _iso()


def _phase_key(phase):
    try:
        if isinstance(phase, bool):
            raise ValueError
        number = int(phase)
    except (TypeError, ValueError):
        raise CoordinationError(f"invalid phase number: {phase!r}") from None
    if number < 0 or number > 999999:
        raise CoordinationError(f"phase number out of range: {number}")
    return f"{number:06d}", number


def _validate_machine_id(machine_id):
    if not isinstance(machine_id, str) or not MACHINE_ID_RE.fullmatch(machine_id):
        raise CoordinationError(
            "machine_id must contain only letters, numbers, '.', '_' or '-' "
            "and be at most 128 characters"
        )
    return machine_id


def _validate_branch(branch):
    if (
        not isinstance(branch, str)
        or not BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith("/")
    ):
        raise CoordinationError(f"invalid coordination branch: {branch!r}")
    return branch


def _validate_remote(remote):
    if not isinstance(remote, str) or not REMOTE_RE.fullmatch(remote):
        raise CoordinationError(f"invalid Git remote: {remote!r}")
    return remote


def _validate_reservation_id(reservation_id):
    if not isinstance(reservation_id, str) or not RESERVATION_ID_RE.fullmatch(
        reservation_id
    ):
        raise CoordinationError(
            "reservation_id must contain only letters, numbers, '.', '_' or "
            "'-' and be at most 128 characters"
        )
    return reservation_id


def _validate_generation(generation):
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise CoordinationError("generation must be a positive integer")
    return generation


def _validate_status(status):
    if status not in STATUSES:
        raise CoordinationError(f"invalid reservation status: {status!r}")
    return status


def _safe_detail(text, limit=500):
    """Keep diagnostics useful without echoing a credential-bearing URL."""
    text = (text or "").replace("\n", " ").strip()
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", text)
    return text[:limit]


def git_binary():
    """The git command named in the loaded config."""
    return conventions.setting("commands.git")


def _git(repo, *args, timeout=GIT_TIMEOUT_S):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            [git_binary(), "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CoordinationUnavailable("git is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CoordinationUnavailable(
            f"git {' '.join(args)} timed out after {timeout}s"
        ) from exc
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_git_repo(path):
    rc, out, _ = _git(path, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def _remote_urls(repo, remote, *, push=False):
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.extend(["--all", remote])
    rc, out, err = _git(repo, *args)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _canonical_remote_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value and not re.match(r"^[^/]+@[^:]+:", value):
        return str(Path(value).expanduser().resolve())
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme:
        return value
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.query,
            parsed.fragment,
        )
    )


def _remote_url_sets(root, private, remote):
    public_fetch = {_canonical_remote_url(url) for url in _remote_urls(root, remote)}
    public_push = {
        _canonical_remote_url(url) for url in _remote_urls(root, remote, push=True)
    }
    private_fetch = {
        _canonical_remote_url(url) for url in _remote_urls(private, remote)
    }
    private_push = {
        _canonical_remote_url(url) for url in _remote_urls(private, remote, push=True)
    }
    return public_fetch, public_push, private_fetch, private_push


def _assert_private_boundary(root, private, remote):
    public_is_repo = _is_git_repo(root)
    if not public_is_repo:
        return
    public_fetch, public_push, private_fetch, private_push = _remote_url_sets(
        root, private, remote
    )
    if not public_fetch or not public_push or not private_fetch or not private_push:
        raise CoordinationUnavailable(
            "cannot prove separate public/private Git destinations"
        )
    if (
        public_push & private_push
        or public_push & private_fetch
        or public_fetch & private_push
    ):
        raise CoordinationUnavailable(
            "public and private repositories share a Git push/fetch destination"
        )


def _head(repo):
    rc, out, _ = _git(repo, "rev-parse", "HEAD")
    return out if rc == 0 and out else None


def _empty_state():
    return {"version": COORDINATION_VERSION, "phases": {}}


def _validate_state(state):
    if not isinstance(state, dict):
        raise CoordinationUnavailable("coordination state is not an object")
    if state.get("version") != COORDINATION_VERSION:
        raise CoordinationUnavailable(
            f"unsupported coordination state version: {state.get('version')!r}"
        )
    phases = state.get("phases")
    if not isinstance(phases, dict):
        raise CoordinationUnavailable("coordination state.phases is not an object")
    for key, record in phases.items():
        if not isinstance(key, str) or not PHASE_KEY_RE.fullmatch(key):
            raise CoordinationUnavailable(f"invalid phase key in state: {key!r}")
        if not isinstance(record, dict):
            raise CoordinationUnavailable(f"phase {key} record is not an object")
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
        for field in ("machine_id", "reservation_id", "phase", "generation"):
            if field not in publication:
                raise CoordinationUnavailable(f"publication lock is missing {field}")
        _validate_machine_id(publication["machine_id"])
        _validate_reservation_id(publication["reservation_id"])
        _phase_key(publication["phase"])
        _validate_generation(publication["generation"])
    return state


def _read_state_file(path, *, allow_missing=True):
    try:
        raw = path.read_text()
    except FileNotFoundError:
        if allow_missing:
            return _empty_state()
        raise CoordinationUnavailable("coordination state file is missing")
    except OSError as exc:
        raise CoordinationUnavailable(f"cannot read coordination state: {exc}") from exc
    try:
        state = json.loads(raw)
    except ValueError as exc:
        raise CoordinationUnavailable(
            f"coordination state does not parse: {exc}"
        ) from exc
    return _validate_state(state)


def _write_state_file(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _owner_matches(record, machine_id, reservation_id, generation):
    return (
        record.get("machine_id") == machine_id
        and record.get("reservation_id") == reservation_id
        and record.get("generation") == generation
    )


def _reservation_from_record(record):
    return {
        "phase": str(record.get("_phase", "")),
        "status": record.get("status"),
        "machine_id": record.get("machine_id"),
        "reservation_id": record.get("reservation_id"),
        "generation": record.get("generation"),
        "run_id": record.get("run_id"),
        "base_revisions": record.get("base_revisions"),
    }


def _cooldown_remaining(record, number, now=None):
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
    moment = _as_moment(now) or _datetime.datetime.now(_datetime.timezone.utc)
    delta = (until - moment).total_seconds()
    if delta <= 0:
        return 0
    return math.ceil(delta)


def _claim_window(record):
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


def _claim_window_passed(record, moment):
    measured = _claim_window(record)
    if measured is None:
        return False
    reference, window = measured
    return (moment - reference).total_seconds() > window


def _expire_records(state, moment):
    """Move every stale group to ``EXPIRED_STATUS`` under one cooldown stamp.

    The members of one reservation share a reservation id, so the group is
    expired as a unit: one member past its window expires all of them, and the
    old owner is fenced for every member at once. Returns the expired records,
    each with its phase key, so the caller can report what moved.
    """
    groups = {}
    for key, record in state["phases"].items():
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        reservation_id = record.get("reservation_id")
        if not reservation_id:
            continue
        groups.setdefault((record.get("machine_id"), reservation_id), []).append(
            (key, record)
        )
    expired = []
    for members in groups.values():
        if not any(_claim_window_passed(record, moment) for _key, record in members):
            continue
        until = _iso(moment + _datetime.timedelta(seconds=GROUP_COOLDOWN_S))
        for key, record in members:
            record["status"] = EXPIRED_STATUS
            record[COOLDOWN_UNTIL_FIELD] = until
            expired.append(
                {
                    "phase": key,
                    "machine_id": record.get("machine_id"),
                    "reservation_id": record.get("reservation_id"),
                }
            )
    return expired


def _claim_one(
    state,
    key,
    machine_id,
    reservation_id,
    generation,
    run_id,
    revisions,
    status,
    number,
    now=None,
):
    """Write or refresh one claim in ``state``; return its phase record.

    One helper backs both ``reserve`` and ``reserve_group`` so a single claim
    and a group claim cannot drift apart about who may claim what. A terminal
    record, another owner, and an active cooldown all raise. ``_transaction``
    runs the whole mutation before it writes anything, so a raise leaves the
    board untouched: a group is claimed atomically or not at all, and half a
    group is never reserved.
    """
    existing = state["phases"].get(key)
    if existing is not None:
        if existing.get("status") == EXPIRED_STATUS:
            remaining = _cooldown_remaining(existing, number, now)
            if remaining > 0:
                raise ReservationCooldown(number, remaining, existing)
            # The cooldown has passed, so the expired record is claimable again
            # and is overwritten below with a fresh reservation.
        elif existing.get("status") in TERMINAL_STATUSES:
            raise ReservationConflict(
                number, existing, f"phase is retained as {existing.get('status')}"
            )
        elif _owner_matches(existing, machine_id, reservation_id, generation):
            existing["status"] = status
            existing["heartbeat_at"] = _iso(now)
            return existing
        else:
            remaining = _cooldown_remaining(existing, number, now)
            if remaining > 0:
                raise ReservationCooldown(number, remaining, existing)
            raise ReservationConflict(number, existing)
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
        "run_id": run_id or f"phase-{key}",
        "base_revisions": revisions,
    }
    state["phases"][key] = record
    return record


def _branch_setting(branch):
    """Resolve the board's branch.

    A machine may name a different one. Nothing in this module can tell whether
    every machine agreed, because two boards on two branch names each look
    perfectly healthy from the inside, so the warning about it belongs to the
    driver, which is what a person reads. It is deliberately not printed here:
    callers merge this module's stderr into files they then parse as JSON, and
    a library that writes to a stream it was handed corrupts them.
    """
    return _validate_branch(branch or conventions.setting("board.branch"))


def _remote_setting(remote):
    return _validate_remote(remote or conventions.setting("board.remote"))


def _resolve_private_repo(repo_root, private_repo=None):
    root = Path(repo_root).resolve()
    named = private_repo
    private = Path(named).resolve() if named else board_repo(root)
    if private is None:
        raise CoordinationUnavailable("reservation board repository is not configured")
    if not private.is_dir() or not _is_git_repo(private):
        raise CoordinationUnavailable(
            f"configured board checkout is unavailable: {private}"
        )
    return root, private


def _integration_branch(private, remote):
    """The branch run state and phase notes are published to.

    The board is not on it, so anything that has to confirm a *published* file
    has to ask this branch rather than the board's own. Asking the board's
    branch used to work only because the two were the same ref.
    """
    rc, out, _err = _git(
        private, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"
    )
    # --short answers with the remote-tracking short name, which still carries
    # the remote prefix; only the branch half is a branch.
    named = out.strip()
    if rc == 0 and named.startswith(f"{remote}/"):
        named = named[len(remote) + 1 :]
    if rc == 0 and named:
        return named
    return INTEGRATION_FALLBACK


def _integration_ref(private, remote):
    """The commit a new board branch is seeded from.

    The board used to live on the integration branch, so the first board branch
    has to start from wherever that file is now. It must be the integration
    branch's own tip and nothing else: the local checkout's HEAD is whatever
    that checkout happens to be on, which during a phase run is a feature
    branch, so seeding from it would root the shared board's history at an
    arbitrary commit.

    The branch is fetched before it is resolved, because a clone that has never
    fetched that branch holds no remote-tracking ref for it and the answer would
    be the same arbitrary HEAD. Failing here is the right answer: seeding from a
    known ref is reproducible, and this is the one step that decides where every
    machine's board history begins.
    """
    branch = _integration_branch(private, remote)
    remote_ref = f"refs/remotes/{remote}/{branch}"
    _git(
        private,
        "fetch",
        "--quiet",
        remote,
        f"+refs/heads/{branch}:{remote_ref}",
        timeout=FETCH_TIMEOUT_S,
    )
    rc, commit, err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc == 0 and commit.strip():
        return commit.strip()
    raise CoordinationUnavailable(
        f"cannot resolve the integration branch {branch!r} on {remote}, "
        f"which is the only commit a new coordination branch may be seeded "
        f"from: {_safe_detail(err or commit)}"
    )


def _board_branch_exists(private, remote, branch):
    rc, _out, _err = _git(
        private,
        "ls-remote",
        "--exit-code",
        "--heads",
        remote,
        branch,
        timeout=FETCH_TIMEOUT_S,
    )
    return rc == 0


def ensure_board_branch(private, remote, branch):
    """Create the board's branch from the integration branch, once.

    Without this the first transaction after the change fails closed on every
    machine, because the branch the board moved to does not exist yet. Seeding
    copies the whole tree at the integration branch's tip, so the existing
    The configured board file comes with it and no live reservation is lost.
    The file stays on the integration branch afterwards as a historical record;
    nothing reads it there.

    This is the only function here that writes to the remote outside a board
    transaction, and it is deliberately not reachable from a read. A read that
    could create the branch would let an inspection of the board publish to the
    remote, which fails on a machine with read-only credentials and reports
    itself as a fetch failure rather than as a permission problem.

    Returns True when it created the branch. A machine that loses the race
    finds the branch already there and does nothing, which is the same answer.
    """
    if _board_branch_exists(private, remote, branch):
        return False
    source = _integration_ref(private, remote)
    rc, _out, err = _git(
        private,
        "push",
        remote,
        f"{source}:refs/heads/{branch}",
        timeout=FETCH_TIMEOUT_S,
    )
    if rc == 0:
        print(
            f"coordination branch {branch!r} seeded from {source[:12]}", file=sys.stderr
        )
        return True
    # Losing the seed race is success: the other machine made the branch.
    if _board_branch_exists(private, remote, branch):
        return False
    raise CoordinationUnavailable(
        f"cannot create coordination branch {branch!r} on {remote}: {_safe_detail(err)}"
    )


def _fetch_branch_ref(private, remote, branch):
    """Fetch one branch and return its remote-tracking ref and commit.

    A branch the remote does not have is the single most common answer here on a
    machine that has not migrated, so it is told apart from a transport failure.
    Git reports a missing branch as a failed fetch, and "coordination fetch
    failed" sent whoever read the log looking for a network problem instead of
    naming the one thing that creates it. The extra round trip is paid only when
    the fetch has already failed.
    """
    remote_ref = f"refs/remotes/{remote}/{branch}"
    refspec = f"+refs/heads/{branch}:{remote_ref}"
    rc, out, err = _git(
        private, "fetch", "--prune", remote, refspec, timeout=FETCH_TIMEOUT_S
    )
    if rc != 0:
        if not _board_branch_exists(private, remote, branch):
            raise CoordinationUnavailable(
                f"coordination branch {branch!r} does not exist on {remote}"
            )
        raise CoordinationUnavailable(
            f"coordination fetch failed: {_safe_detail(err or out)}"
        )
    rc, commit, err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc != 0 or not commit:
        raise CoordinationUnavailable(
            f"coordination branch {branch!r} does not exist on {remote}"
        )
    return remote_ref, commit


def _state_initialized(private, ref):
    shallow_rc, shallow_out, _shallow_err = _git(
        private, "rev-parse", "--is-shallow-repository"
    )
    if shallow_rc == 0 and shallow_out.strip() == "true":
        raise CoordinationUnavailable(
            "coordination state history is unavailable in a shallow repository"
        )
    rc, out, err = _git(
        private,
        "log",
        "--diff-filter=A",
        "--format=%H",
        "-n",
        "1",
        ref,
        "--",
        state_rel().as_posix(),
    )
    if rc != 0:
        raise CoordinationUnavailable(
            f"cannot inspect coordination state history: {_safe_detail(err or out)}"
        )
    return bool(out.strip())


class _Checkout:
    """A disposable coordination checkout, and whether it is worth keeping.

    ``keep`` is the policy switch, and it is set by the caller rather than
    inferred here, because only the caller knows what the checkout ended up
    holding. A checkout whose board state was committed and whose push the
    remote rejected holds a state the remote never received, which is the one
    case a person might want to look at. A checkout whose commit failed holds
    nothing at all, because the commit is what would have held it.
    """

    def __init__(self, private, parent, path):
        self.private = private
        self.parent = parent
        self.path = path
        self.keep = False
        #: Set when the checkout could not be removed, so the caller can name
        #: the folder in whatever it reports about the failure.
        self.stranded = None

    def __fspath__(self):
        return str(self.path)


@contextlib.contextmanager
def _detached_worktree(private, ref):
    """Yield a disposable checkout without taking the phase branch."""
    parent = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX))
    checkout = _Checkout(private, parent, parent / "checkout")
    rc, _, err = _git(private, "worktree", "add", "--detach", str(checkout.path), ref)
    if rc != 0:
        shutil.rmtree(parent, ignore_errors=True)
        raise CoordinationUnavailable(
            f"cannot create coordination worktree: {_safe_detail(err)}"
        )
    try:
        yield checkout
        kept = checkout.keep
    except BaseException:
        # An error inside the block is what makes this leak: the board state has
        # been written and staged, so the tree is dirty and an ordinary remove
        # refuses. There is nothing to inspect, because the commit that would
        # have held the state does not exist. Reset first, so the same removal
        # that works on the happy path works here too.
        _discard_checkout_contents(checkout)
        _remove_checkout(checkout)
        raise
    if kept:
        # Deliberate: the local commit is the only copy of a board state the
        # remote never received, so it stays for a person to read.
        print(
            f"coordination checkout kept for inspection: {checkout.parent}",
            file=sys.stderr,
        )
        return
    _remove_checkout(checkout)


def _discard_checkout_contents(checkout):
    """Make a dirty checkout clean again, so an ordinary removal can succeed."""
    _git(checkout.path, "reset", "--hard", "--quiet", "HEAD")
    _git(checkout.path, "clean", "-fdq")


def _remove_checkout(checkout):
    """Remove the checkout and its folder, and report it if that is impossible.

    The path is created and owned here. Removing it must not touch the phase
    checkout, even when the branch is named master, so the removal always names
    the path and never the branch.
    """
    rc, _, _ = _git(checkout.private, "worktree", "remove", str(checkout.path))
    if rc == 0:
        shutil.rmtree(checkout.parent, ignore_errors=True)
        return
    # The folder is still there, so prune alone would drop nothing: the
    # registration is accurate. Force the removal, because a directory this
    # function created and no transaction is using has no value, then prune in
    # case the force left an entry behind.
    forced, _, _ = _git(
        checkout.private, "worktree", "remove", "--force", str(checkout.path)
    )
    _git(checkout.private, "worktree", "prune")
    if forced == 0:
        shutil.rmtree(checkout.parent, ignore_errors=True)
        return
    print(
        f"coordination checkout could not be removed and is still at "
        f"{checkout.parent}; the sweep clears it once it is "
        f"{WORKTREE_SWEEP_AGE_S // 60} minutes old",
        file=sys.stderr,
    )


def sweep_stale_worktrees(now=None, age_s=WORKTREE_SWEEP_AGE_S, root=None):
    """Delete leftover coordination checkouts older than ``age_s``.

    A checkout is left behind when the machine lost power mid-transaction, and
    nothing else ever returns for it. This runs before each transaction so the
    cost of a crash is one folder rather than one per tick for ever. It only
    touches directories this module created, matched by prefix, and only ones
    old enough that no live transaction can still be using them.

    Returns ``(removed, bytes)`` so a caller can report the reclaim rather than
    leaving a slow disk loss that nobody attributes.
    """
    parent = Path(root) if root is not None else Path(tempfile.gettempdir())
    moment = (
        now
        if now is not None
        else _datetime.datetime.now(_datetime.timezone.utc).timestamp()
    )
    removed, reclaimed = 0, 0
    try:
        candidates = list(parent.glob(f"{WORKTREE_PREFIX}*"))
    except OSError:
        return 0, 0
    for candidate in candidates:
        try:
            age = moment - candidate.stat().st_mtime
        except OSError:
            continue
        if age < age_s:
            continue
        reclaimed += _directory_bytes(candidate)
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed, reclaimed


def _directory_bytes(path):
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _commit_state(checkout, message):
    rc, _, err = _git(checkout, "add", "--", state_rel().as_posix())
    if rc != 0:
        raise CoordinationUnavailable(
            f"cannot stage coordination state: {_safe_detail(err)}"
        )
    rc, out, err = _git(checkout, "commit", "-q", "-m", message)
    if rc != 0:
        combined = _safe_detail(f"{out} {err}")
        if "nothing to commit" in combined.lower():
            return None
        raise CoordinationUnavailable(f"coordination state commit failed: {combined}")
    rc, commit, _ = _git(checkout, "rev-parse", "HEAD")
    if rc != 0 or not commit:
        raise CoordinationUnavailable("coordination commit has no revision")
    return commit


def _transaction(private, remote, branch, mutate, message):
    """Run a read-modify-push transaction, retrying only rejected pushes."""
    last_error = None
    # A transaction is a write, so it is the path allowed to create the board's
    # branch. It is done once here rather than per attempt, because the first
    # attempt either finds the branch or creates it and no attempt after that
    # has anything left to decide.
    ensure_board_branch(private, remote, branch)
    swept, reclaimed = sweep_stale_worktrees()
    if swept:
        # A leak then reads as a number on the tick that found it, rather than
        # as a disk filling up that nobody attributes to anything.
        print(
            f"coordination sweep removed {swept} stale checkout(s), "
            f"{reclaimed // (1024 * 1024)} MB reclaimed",
            file=sys.stderr,
        )
    for attempt in range(MAX_TRANSACTION_ATTEMPTS):
        ref, _commit = _fetch_branch_ref(private, remote, branch)
        initialized = _state_initialized(private, ref)
        with _detached_worktree(private, ref) as checkout:
            state = _read_state_file(
                state_path(checkout.path), allow_missing=not initialized
            )
            mutation = mutate(state)
            if not mutation.changed:
                return mutation.value
            _write_state_file(state_path(checkout.path), state)
            new_commit = _commit_state(checkout.path, message)
            if new_commit is None:
                return mutation.value
            rc, out, err = _git(
                checkout.path,
                "push",
                remote,
                f"HEAD:refs/heads/{branch}",
                timeout=FETCH_TIMEOUT_S,
            )
            # The final attempt's commit exists and the remote refused it, so its
            # folder holds the only copy of a board state nobody else has. The
            # earlier attempts are not kept: each one holds an intermediate state
            # the final attempt already superseded, and each is a whole checkout
            # of the private repository, so keeping them costs four directories
            # per rejected transaction to preserve nothing the last one lacks.
            checkout.keep = rc != 0 and attempt == MAX_TRANSACTION_ATTEMPTS - 1
        if rc == 0:
            return mutation.value
        last_error = _safe_detail(f"{out} {err}")
    raise CoordinationUnavailable(
        f"coordination state push failed after retries: {last_error or 'unknown error'}"
    )


def _base_revisions(root, private):
    public = _head(root) if _is_git_repo(root) else None
    private_revision = _head(private)
    return {"public": public, "private": private_revision}


class ReservationStore:
    """Read and mutate the shared reservation state for one repository."""

    def __init__(self, repo_root, private_repo=None, branch=None, remote=None):
        self.root, self.private = _resolve_private_repo(repo_root, private_repo)
        self.branch = _branch_setting(branch)
        self.remote = _remote_setting(remote)
        _assert_private_boundary(self.root, self.private, self.remote)

    def migrate_board(self):
        """Create the board's branch if the remote does not have it yet.

        This is the deliberate way the branch comes into being, and it is named
        so that a caller says out loud that it means to write. The alternative
        it replaces was a read that created the branch as a side effect, which
        let inspecting the board publish to the remote.

        Returns True when this call created the branch.
        """
        return ensure_board_branch(self.private, self.remote, self.branch)

    def read(self):
        ref, commit = _fetch_branch_ref(self.private, self.remote, self.branch)
        initialized = _state_initialized(self.private, ref)
        exists_rc, _, _ = _git(
            self.private, "cat-file", "-e", f"{ref}:{state_rel().as_posix()}"
        )
        if exists_rc != 0:
            if initialized:
                raise CoordinationUnavailable(
                    "coordination state file was deleted from an initialized board"
                )
            # A board may be initialized before its first claim. The commit
            # must exist, but an absent state file is an empty state.
            return _empty_state(), commit
        rc, raw, err = _git(self.private, "show", f"{ref}:{state_rel().as_posix()}")
        if rc != 0:
            raise CoordinationUnavailable(
                f"cannot read coordination state: {_safe_detail(err or raw)}"
            )
        try:
            state = json.loads(raw)
        except ValueError as exc:
            raise CoordinationUnavailable(
                f"coordination state does not parse: {exc}"
            ) from exc
        return _validate_state(state), commit

    def _reserve_mutation(
        self, phase, machine_id, reservation_id, generation, run_id, revisions, status
    ):
        key, number = _phase_key(phase)
        machine_id = _validate_machine_id(machine_id)
        _validate_status(status)
        if status not in ACTIVE_STATUSES:
            raise CoordinationError("a new reservation must start as active")

        def mutate(state):
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
        phase,
        machine_id,
        *,
        reservation_id=None,
        generation=None,
        run_id=None,
        base_revisions=None,
        status="claimed",
    ):
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

    def reserve_group(
        self,
        phases,
        machine_id,
        *,
        reservation_id=None,
        generation=None,
        run_ids=None,
        base_revisions=None,
        status="claimed",
        now=None,
    ):
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
        keys = []
        for phase in phases:
            key, _number = _phase_key(phase)
            if key in keys:
                raise CoordinationError(f"phase {key} appears twice in one group claim")
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

        def mutate(state):
            records = []
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
                records.append(_reservation_from_record({**record, "_phase": key}))
            return _Mutation(True, records)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            f"phase coordination: reserve group [{','.join(keys)}] for {machine_id}",
        )

    def expire_stale(self, now=None):
        """Return every stale group to the queue under a cooldown stamp.

        A claim is stale when no work started inside the start window, or, once
        work has begun, when its newest heartbeat is older than the heartbeat
        window. Every member of one reservation moves in a single board write,
        so a group is never half expired. The window is measured from an
        injected clock when a test passes one. Returns the expired members as
        ``{"phase", "machine_id", "reservation_id"}`` dicts; an empty list means
        nothing was stale and no write was made.
        """
        moment = _as_moment(now) or _datetime.datetime.now(_datetime.timezone.utc)

        def mutate(state):
            expired = _expire_records(state, moment)
            return _Mutation(bool(expired), expired)

        return _transaction(
            self.private,
            self.remote,
            self.branch,
            mutate,
            "phase coordination: expire stale claims",
        )

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

    def takeover(
        self,
        phase,
        machine_id,
        *,
        confirm=False,
        run_id=None,
        base_revisions=None,
        expected_reservation_id=None,
        expected_generation=None,
    ):
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
        current = current_state["phases"].get(key)
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

        def mutate(state):
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

    def register_running(self, phase, machine_id, *, confirm=False, run_id=None):
        """Register a pre-coordination run as an active server claim."""
        if not confirm:
            raise CoordinationError("registering a running phase requires confirm=true")
        return self.reserve(phase, machine_id, run_id=run_id, status="running")

    def acquire_publication(self, reservation):
        key, _number = _phase_key(reservation.get("phase"))
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))

        def mutate(state):
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
            if current is not None:
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

    def release_publication(self, reservation):
        key, _number = _phase_key(reservation.get("phase"))
        machine_id = _validate_machine_id(reservation.get("machine_id"))
        reservation_id = _validate_reservation_id(reservation.get("reservation_id"))
        generation = _validate_generation(reservation.get("generation"))

        def mutate(state):
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


def reservation_file(repo_root, phase):
    """Return the token beside this phase's pipeline run record."""
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / "reservation.json"


def publication_file(repo_root, phase):
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / PUBLICATION_NAME


def _receipt_commit(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise CoordinationError(f"publication receipt has invalid {field}")
    return value.lower()


def _validate_publication_receipt(
    path, reservation, private_repo, remote, branch, *, repo_root=None
):
    path = Path(path).resolve()
    key, _number = _phase_key(reservation.get("phase"))
    expected = (
        reservation_file(repo_root or conventions.setting("repo-root"), _number).parent
        / PUBLICATION_NAME
    ).resolve()
    if path != expected:
        raise CoordinationError(
            "publication receipt is outside the phase run directory"
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"publication receipt cannot be read: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("state") != "published"
    ):
        raise CoordinationError("publication receipt is not a published receipt")
    if data.get("phase") != key:
        raise CoordinationError("publication receipt phase does not match reservation")
    owner = data.get("reservation")
    if not isinstance(owner, dict) or any(
        owner.get(field) != reservation.get(field)
        for field in ("machine_id", "reservation_id", "generation")
    ):
        raise CoordinationError("publication receipt does not match reservation")
    for field in ("public_commit", "private_commit"):
        _receipt_commit(data.get(field), field)
    if not isinstance(data.get("published_at"), str) or not data["published_at"]:
        raise CoordinationError("publication receipt has no publication time")
    # The receipt is run state, published to the integration branch, so it is
    # confirmed against that branch. It used to be confirmed against the board
    # branch, which worked only while the two were the same ref.
    published = _integration_branch(private_repo, remote)
    ref, _commit = _fetch_branch_ref(private_repo, remote, published)
    rel = expected.relative_to(Path(private_repo).resolve()).as_posix()
    rc, raw, err = _git(private_repo, "show", f"{ref}:{rel}")
    if rc != 0:
        raise CoordinationUnavailable(
            f"publication receipt is not present on the private remote's "
            f"{published} branch: {_safe_detail(err or raw)}"
        )
    try:
        remote_data = json.loads(raw)
    except ValueError as exc:
        raise CoordinationUnavailable(
            "remote publication receipt does not parse"
        ) from exc
    if remote_data != data:
        raise CoordinationUnavailable("local and remote publication receipts differ")
    return {
        "path": str(path),
        "public_commit": data["public_commit"].lower(),
        "private_commit": data["private_commit"].lower(),
        "published_at": data["published_at"],
    }


def save_reservation_file(path, reservation):
    """Persist the fence token before a phase process is launched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "phase": reservation["phase"],
        "machine_id": reservation["machine_id"],
        "reservation_id": reservation["reservation_id"],
        "generation": reservation["generation"],
        "run_id": reservation.get("run_id"),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return payload


def load_reservation_file(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"reservation file is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise CoordinationError("reservation file is not an object")
    for field in ("phase", "machine_id", "reservation_id", "generation"):
        if field not in data:
            raise CoordinationError(f"reservation file is missing {field}")
    _validate_machine_id(data["machine_id"])
    _validate_reservation_id(data["reservation_id"])
    _validate_generation(data["generation"])
    return data


def _print_json(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def _cli_store(args):
    return ReservationStore(conventions.setting("repo-root"))


def _self_test_check(checks, name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _git_test_repo(base, name, initial_state=None):
    """Create one local bare remote and one client checkout for self-tests."""
    remote = base / f"{name}-remote.git"
    subprocess.run(
        [git_binary(), "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/master")
    seed = base / f"{name}-seed"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(seed)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(seed, "config", "user.email", "coordination-test@example.com")
    _git(seed, "config", "user.name", "coordination test")
    _git(seed, "checkout", "-q", "-b", "master")
    (seed / "seed.txt").write_text("seed\n")
    if initial_state is not None:
        _write_state_file(seed / state_rel(), initial_state)
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "coordination seed")
    _git(seed, "push", "-q", "-u", "origin", "master")
    clients = []
    for index in (1, 2):
        client = base / f"{name}-client-{index}"
        subprocess.run(
            [git_binary(), "clone", "-q", str(remote), str(client)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(client, "config", "user.email", "coordination-test@example.com")
        _git(client, "config", "user.name", "coordination test")
        clients.append(client)
    return remote, clients


def _group_lifecycle_checks(checks, base):
    """Prove release, expiry, cooldown and fencing on a two-machine fixture.

    Time is injected through the stores rather than slept through, so the
    start window, the heartbeat window and the cooldown are all exercised in a
    single run. Machine A claims and works; machine B is the second owner.
    """
    import datetime as _dt

    _remote, clients = _git_test_repo(base, "groups")
    root_a = base / "groups-root-a"
    root_b = base / "groups-root-b"
    root_a.mkdir()
    root_b.mkdir()
    store_a = ReservationStore(root_a, private_repo=clients[0])
    store_b = ReservationStore(root_b, private_repo=clients[1])
    epoch = _dt.datetime(2026, 10, 8, 12, 0, tzinfo=_dt.timezone.utc)

    def at(seconds):
        return epoch + _dt.timedelta(seconds=seconds)

    members = ["100200", "100201", "100202"]

    # A group claimed before its start window is live: another machine is
    # refused with a plain conflict, not a cooldown.
    records = store_a.reserve_group(members, "server-01", now=at(0))
    shared = {record["reservation_id"] for record in records}
    _self_test_check(
        checks,
        "a group is claimed under one shared reservation id",
        len(records) == 3 and len(shared) == 1,
        str(records),
    )
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S - 1))
        early = None
    except ReservationConflict as exc:
        early = exc
    except ReservationCooldown as exc:  # pragma: no cover - clearer failure
        early = exc
    _self_test_check(
        checks,
        "a live group cannot be claimed by a second machine",
        isinstance(early, ReservationConflict),
        f"early={early!r}",
    )

    # Past the start window the whole group expires in one sweep, and the
    # request that found it is refused during the cooldown.
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S + 1))
        cooled = None
    except ReservationCooldown as exc:
        cooled = exc
    state, _commit = store_a.read()
    statuses = [state["phases"].get(key, {}).get("status") for key in members]
    _self_test_check(
        checks,
        "an expired group is refused during the cooldown",
        cooled is not None
        and cooled.remaining_seconds > 0
        and statuses == [EXPIRED_STATUS] * 3,
        f"cooled={cooled!r} statuses={statuses}",
    )
    until = state["phases"][members[0]].get(COOLDOWN_UNTIL_FIELD)
    _self_test_check(
        checks,
        "every member of the expired group shares one cooldown stamp",
        len({state["phases"][key].get(COOLDOWN_UNTIL_FIELD) for key in members}) == 1,
        f"cooldown_until={until!r}",
    )

    # A request never shortens or extends the cooldown.
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S + 2))
        cooled_again = None
    except ReservationCooldown as exc:
        cooled_again = exc
    state_again, _commit = store_a.read()
    until_again = state_again["phases"][members[0]].get(COOLDOWN_UNTIL_FIELD)
    _self_test_check(
        checks,
        "a request during cooldown never moves the deadline",
        cooled_again is not None and until_again == until,
        f"{until!r} -> {until_again!r}",
    )

    # A late write from the old owner is fenced after expiry.
    late = {
        "machine_id": "server-01",
        "reservation_id": records[0]["reservation_id"],
        "generation": records[0]["generation"],
    }
    try:
        store_a.renew_group(late, now=at(GROUP_START_WINDOW_S + 2))
        late_fenced = False
    except ReservationFenced:
        late_fenced = True
    _self_test_check(
        checks,
        "a late renew after expiry is fenced",
        late_fenced,
        "expected ReservationFenced",
    )

    # Once the cooldown passes the second machine claims the group.
    later = at(GROUP_START_WINDOW_S + GROUP_COOLDOWN_S + 1)
    after = store_b.reserve_group(members, "server-02", now=later)
    _self_test_check(
        checks,
        "the group is claimable once its cooldown passes",
        [record["phase"] for record in after] == members
        and {record["reservation_id"] for record in after}.isdisjoint(shared),
        str(after),
    )

    # A running group is measured from its heartbeat. renew_group moves the
    # heartbeat for every member at once, which keeps the group alive past the
    # point its original stamp would have expired it.
    running = ["100210", "100211"]
    running_records = store_a.reserve_group(
        running, "server-03", status="running", now=at(0)
    )
    running_token = {
        "machine_id": "server-03",
        "reservation_id": running_records[0]["reservation_id"],
        "generation": running_records[0]["generation"],
    }
    store_a.renew_group(running_token, now=at(GROUP_HEARTBEAT_WINDOW_S - 100))
    refreshed, _commit = store_a.read()
    _self_test_check(
        checks,
        "renew_group refreshes every member's heartbeat",
        len({refreshed["phases"][key]["heartbeat_at"] for key in running}) == 1,
        f"heartbeats={[refreshed['phases'][key]['heartbeat_at'] for key in running]}",
    )
    try:
        store_b.reserve_group(
            running, "server-04", now=at(GROUP_HEARTBEAT_WINDOW_S + 1)
        )
        still_live = None
    except ReservationCooldown as exc:
        still_live = exc
    except ReservationConflict as exc:
        still_live = exc
    _self_test_check(
        checks,
        "a renewed running group is still live past its first window",
        isinstance(still_live, ReservationConflict),
        f"result={still_live!r}",
    )
    try:
        store_b.reserve_group(
            running, "server-04", now=at(2 * GROUP_HEARTBEAT_WINDOW_S - 99)
        )
        heartbeat_expired = None
    except ReservationCooldown as exc:
        heartbeat_expired = exc
    _self_test_check(
        checks,
        "a running group expires from its heartbeat, not a fixed deadline",
        heartbeat_expired is not None and heartbeat_expired.remaining_seconds > 0,
        f"expired={heartbeat_expired!r}",
    )

    # An explicit release has no cooldown and leaves published work alone.
    published = ["100220", "100221"]
    owned = store_a.reserve_group(published, "server-05", now=at(0))
    token = {
        "machine_id": "server-05",
        "reservation_id": owned[0]["reservation_id"],
        "generation": owned[0]["generation"],
    }
    old_runs = conventions.override({"driver.runs": store_a.private / "runs"})
    try:
        receipt = _self_test_receipt(store_a, owned[1])
        store_a.set_status(owned[1], "completed", evidence_path=receipt)
    finally:
        conventions.configure(old_runs)
    released = store_a.release_group(token, now=at(0))
    after_release, _commit = store_a.read()
    _self_test_check(
        checks,
        "release returns the unfinished member and keeps the published one",
        released == ["100220"]
        and after_release["phases"]["100221"]["status"] == "completed"
        and "100220" not in after_release["phases"],
        f"released={released} phases={sorted(after_release['phases'])}",
    )
    immediate = store_b.reserve_group(["100220"], "server-06", now=at(0))
    _self_test_check(
        checks,
        "an explicit release has no cooldown",
        [record["phase"] for record in immediate] == ["100220"],
        str(immediate),
    )

    # A takeover moves the whole group. One reservation id covers every member,
    # so taking over a single member and leaving the others under the old id
    # would split the group, and release, renewal and expiry all move a group by
    # that shared id.
    takers = ["100230", "100231"]
    taken = store_a.reserve_group(takers, "server-08", now=at(0))
    takeover = store_a.takeover(
        takers[1],
        "server-09",
        confirm=True,
        expected_reservation_id=taken[0]["reservation_id"],
        expected_generation=taken[0]["generation"],
    )
    after_takeover, _commit = store_a.read()
    _self_test_check(
        checks,
        "a takeover moves the whole group under one new reservation id",
        {after_takeover["phases"][key]["reservation_id"] for key in takers}
        == {takeover["reservation_id"]}
        and all(
            after_takeover["phases"][key]["machine_id"] == "server-09" for key in takers
        )
        and takeover["generation"] == taken[0]["generation"] + 1
        and takeover["reservation_id"] != taken[0]["reservation_id"],
        str({key: after_takeover["phases"][key]["reservation_id"] for key in takers}),
    )


def self_test_checks():
    """Exercise the shared board with two local clients; no network or repo state."""
    import threading

    conventions.configure(
        {
            "repo-root": Path("."),
            "pipeline": None,
            "roadmap-dir": Path("roadmap"),
            "driver.runs": Path("runs"),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": Path("."),
            "board.file": Path("claims.json"),
            "board.branch": SELF_TEST_BRANCH,
            "board.remote": "origin",
        }
    )
    checks = []
    # The hygiene fixture writes a `pre-receive` hook and git refuses to run it
    # from a `noexec` mount, so the fixture must live where the host runs a
    # script. A host whose default temporary directory is `noexec` would
    # otherwise report the hook as broken when its mode is correct.
    base = Path(
        make_exec_dir() or tempfile.mkdtemp(prefix="phase-reservations-selftest-")
    )
    try:
        remote, clients = _git_test_repo(base, "claims")
        # Each store owns a different real client checkout. The public side
        # is intentionally absent in this hermetic check; the private repo is
        # the only remote that can receive coordination state.
        root_a = base / "root-a"
        root_b = base / "root-b"
        root_a.mkdir()
        root_b.mkdir()
        store_a = ReservationStore(root_a, private_repo=clients[0])
        store_b = ReservationStore(root_b, private_repo=clients[1])

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim(store, machine):
            barrier.wait()
            try:
                results.append(store.reserve(100601, machine))
            except Exception as exc:  # noqa: BLE001 - test records the failure
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(store_a, "server-01")),
            threading.Thread(target=claim, args=(store_b, "local-01")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        _self_test_check(
            checks,
            "two clients race: one reservation wins",
            len(results) == 1
            and len(errors) == 1
            and isinstance(errors[0], ReservationConflict),
            f"results={len(results)} errors={[type(e).__name__ for e in errors]}",
        )

        state, _commit = store_a.read()
        first = state["phases"].get("100601")
        _self_test_check(
            checks,
            "winning record is complete",
            bool(
                first
                and first.get("reservation_id")
                and first.get("generation") == 1
                and first.get("machine_id") in {"server-01", "local-01"}
            ),
            str(first),
        )

        # The winner can renew/complete; a second phase can be reserved at the
        # same time without changing the first phase's ordering.
        winner_machine = first["machine_id"]
        winner = {
            "phase": "100601",
            "machine_id": winner_machine,
            "reservation_id": first["reservation_id"],
            "generation": first["generation"],
        }
        store_a.renew(winner)
        try:
            store_a.set_status(winner, "completed")
            receipt_required = False
        except CoordinationError:
            receipt_required = True
        _self_test_check(
            checks,
            "completion requires a published receipt",
            receipt_required,
            "missing receipt refused",
        )
        old_runs = conventions.override({"driver.runs": store_a.private / "runs"})
        try:
            receipt = _self_test_receipt(store_a, winner)
            store_a.set_status(winner, "completed", evidence_path=receipt)
            store_a.set_status(winner, "completed", evidence_path=receipt)
        finally:
            conventions.configure(old_runs)
        other = store_b.reserve(100440, "server-01")
        _self_test_check(
            checks,
            "different phases can be reserved independently",
            other["phase"] == "100440",
            str(other),
        )

        # No automatic expiry: an old record remains fenced until the explicit
        # operator takeover increments its generation.
        old = store_a.reserve(100602, "local-01")
        replacement = store_a.takeover(
            100602,
            "server-01",
            confirm=True,
            expected_reservation_id=old["reservation_id"],
            expected_generation=old["generation"],
        )
        try:
            store_a.renew(old)
            fenced = False
        except ReservationFenced:
            fenced = True
        _self_test_check(
            checks,
            "explicit takeover fences old generation",
            fenced and replacement["generation"] == old["generation"] + 1,
            f"old={old} replacement={replacement}",
        )
        try:
            store_a.takeover(
                100602,
                "server-01",
                confirm=True,
                expected_reservation_id=old["reservation_id"],
                expected_generation=old["generation"],
            )
            retry_fenced = False
        except ReservationFenced:
            retry_fenced = True
        _self_test_check(
            checks,
            "takeover retry cannot refence replacement",
            retry_fenced,
            "expected old token",
        )
        rejected = store_a.reserve(100603, "local-01")
        store_a.set_status(rejected, "rejected")
        rejected_takeover = store_a.takeover(
            100603,
            "server-01",
            confirm=True,
            expected_reservation_id=rejected["reservation_id"],
            expected_generation=rejected["generation"],
        )
        _self_test_check(
            checks,
            "rejected work requires explicit takeover",
            rejected_takeover["generation"] == rejected["generation"] + 1,
            f"old={rejected} replacement={rejected_takeover}",
        )

        deleted_ok, deleted_detail = _deleted_state_check(base)
        _self_test_check(
            checks, "deleted state fails closed", deleted_ok, deleted_detail
        )
        key_ok, key_detail = _bad_phase_key_check(base)
        _self_test_check(
            checks, "noncanonical phase keys fail closed", key_ok, key_detail
        )

        # A missing/failed board is unavailable, never treated as an empty
        # board that permits a launch.
        bad = ReservationStore(root_a, private_repo=clients[0], remote="does-not-exist")
        try:
            bad.read()
            unavailable = False
        except CoordinationUnavailable:
            unavailable = True
        _self_test_check(
            checks,
            "coordination fetch failure fails closed",
            unavailable,
            "missing remote",
        )

        lock_contender = store_b.reserve(100604, "server-01")
        pushurl_ok, pushurl_detail = _pushurl_boundary_check(base)
        _self_test_check(
            checks, "public pushurl boundary fails closed", pushurl_ok, pushurl_detail
        )
        _self_test_check(
            checks,
            "publication lock blocks a valid second phase",
            _publication_lock_check(store_a, other, lock_contender),
            "claim-scoped",
        )
        try:
            _group_lifecycle_checks(checks, base)
        except CoordinationError:
            # The shared ``checks`` list already holds whatever the lifecycle
            # proved before it raised, so the named failure survives; this only
            # keeps a fault injected into the lifecycle from aborting the whole
            # self-test before it prints its verdict. A test harness cannot tell
            # a caught guard from a passing one if the run dies first.
            _self_test_check(
                checks,
                "the group lifecycle fixture ran to the end",
                False,
                "the lifecycle raised before it finished",
            )
        _checkout_hygiene_checks(checks, base)
    finally:
        drop_checkouts_under(base)
        shutil.rmtree(base, ignore_errors=True)
    return checks


def _no_identity(private):
    """Take every git identity away, so the commit step cannot succeed."""
    for key in ("user.email", "user.name"):
        _git(private, "config", key, "")


def _worktree_entries(private):
    rc, out, _err = _git(private, "worktree", "list")
    return out if rc == 0 else ""


def _leftover_checkouts():
    return sorted(Path(tempfile.gettempdir()).glob(f"{WORKTREE_PREFIX}*"))


def drop_checkouts_under(path):
    """Remove kept checkouts that belong to a fixture rooted at ``path``.

    A checkout deliberately survives a rejected push, and a self-test provokes
    rejected pushes on purpose. Its fixture is torn down straight afterwards, so
    the checkout is left pointing at a repository that no longer exists and
    nothing will ever sweep it. Finding them by the fixture path in their
    ``.git`` pointer is exact: a checkout belonging to another fixture, or to a
    real machine, is not touched.
    """
    root = str(Path(path).resolve())
    dropped = 0
    for candidate in _leftover_checkouts():
        pointer = candidate / "checkout" / ".git"
        try:
            target = pointer.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if root not in target:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        dropped += 1
    return dropped


def _checkout_hygiene_checks(checks, base):
    """A failed commit must leave no checkout, and a rejected push must leave one.

    The two are opposites on purpose. A commit that failed has no commit, so
    its folder holds nothing and keeping it costs a directory per tick for
    ever. A rejected push holds the only copy of a board state the remote
    never received, so that one is worth a directory.
    """
    # The `pre-receive` hook below is the thing under test, so the fixture root
    # is the caller's executable directory rather than a fresh default-temp
    # directory a `noexec` mount would refuse to run it from.
    sandbox = base / "checkout-hygiene"
    sandbox.mkdir()
    try:
        before = set(_leftover_checkouts())

        # A commit that fails: no folder, and no stale registration either.
        _remote, clients = _git_test_repo(sandbox, "failed-commit")
        private = clients[0]
        root = sandbox / "failed-root"
        root.mkdir()
        _no_identity(private)
        store = ReservationStore(root, private_repo=private)
        try:
            store.reserve(100700, "server-01")
            failed = False
            detail = "the reservation was accepted without a git identity"
        except CoordinationUnavailable as exc:
            failed = "commit failed" in str(exc)
            detail = "commit step failed as intended"
        leaked = sorted(set(_leftover_checkouts()) - before)
        registrations = [
            line
            for line in _worktree_entries(private).splitlines()
            if WORKTREE_PREFIX in line
        ]
        _self_test_check(
            checks,
            "a failed board commit leaves no checkout",
            failed and not leaked,
            f"{detail}; {len(leaked)} leftover checkout(s), "
            f"{len(registrations)} stale registration(s)",
        )
        for path in leaked:
            shutil.rmtree(path, ignore_errors=True)
        _git(private, "worktree", "prune")

        # A rejected push: the local commit is the only copy, so it is kept and
        # the path is reported rather than discarded.
        rejected_root = sandbox / "rejected-root"
        rejected_root.mkdir()
        _remote2, clients2 = _git_test_repo(sandbox, "rejected-push")
        keeper = clients2[0]
        rejected = ReservationStore(rejected_root, private_repo=keeper)
        # One reservation first, so the board branch exists. A hook that refuses
        # every push would otherwise also refuse the push that creates the
        # branch, and the case would be measuring the seed rather than the
        # rejection.
        rejected.reserve(100700, "server-01")
        # A pre-receive hook on the bare remote is the honest way to reject a
        # push that git itself considers well formed.
        subprocess.run(
            [
                git_binary(),
                "-C",
                str(_remote2),
                "config",
                "core.hooksPath",
                str(sandbox / "hooks"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        hooks = sandbox / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-receive").write_text("#!/bin/sh\ncat >/dev/null\nexit 1\n")
        (hooks / "pre-receive").chmod(0o755)
        before_rejected = set(_leftover_checkouts())
        try:
            rejected.reserve(100701, "server-01")
            raised, reason = False, "the push was accepted"
        except CoordinationUnavailable as exc:
            raised = "push failed" in str(exc)
            reason = str(exc).splitlines()[0][:90]
        kept = sorted(set(_leftover_checkouts()) - before_rejected)
        # Exactly one, not "at least one": the retry loop makes four attempts and
        # only the last holds a state the remote refused. Keeping the earlier
        # ones is what this count exists to catch.
        _self_test_check(
            checks,
            "a rejected push keeps only the final attempt's checkout",
            raised and len(kept) == 1,
            f"{reason}; {len(kept)} checkout(s) kept",
        )
        for path in kept:
            shutil.rmtree(path, ignore_errors=True)
        _git(keeper, "worktree", "prune")

        # The sweep takes an old folder and leaves a recent one, because two
        # runs on one machine must not delete each other's.
        sweep_root = sandbox / "sweep"
        sweep_root.mkdir()
        old_dir = sweep_root / f"{WORKTREE_PREFIX}old"
        new_dir = sweep_root / f"{WORKTREE_PREFIX}new"
        for candidate, age in ((old_dir, 7200), (new_dir, 0)):
            candidate.mkdir()
            (candidate / "payload").write_text("x" * 4096)
            moment = _datetime.datetime.now(_datetime.timezone.utc).timestamp() - age
            os.utime(candidate, (moment, moment))
        removed, reclaimed = sweep_stale_worktrees(root=sweep_root)
        remaining = sorted(p.name for p in sweep_root.iterdir())
        _self_test_check(
            checks,
            "the sweep takes an old checkout and spares a new one",
            removed == 1 and reclaimed > 0 and remaining == [new_dir.name],
            f"removed {removed}, reclaimed {reclaimed} bytes, remaining {remaining}",
        )
        # Only this module's own prefix is ever touched.
        other = sweep_root / "someone-elses-directory"
        other.mkdir()
        moment = _datetime.datetime.now(_datetime.timezone.utc).timestamp() - 7200
        os.utime(other, (moment, moment))
        sweep_stale_worktrees(root=sweep_root)
        _self_test_check(
            checks,
            "the sweep spares a directory it did not create",
            other.exists(),
            str(other.name),
        )
        _board_branch_checks(checks, sandbox)
    finally:
        drop_checkouts_under(sandbox)
        shutil.rmtree(sandbox, ignore_errors=True)


def _board_branch_checks(checks, base):
    """The board gets its own branch, and moving it must not move run state.

    The defect this replaces was a board commit landing on the branch a phase's
    own commits are based on, so publishing the phase raced the bookkeeping. The
    property to pin is that a reservation leaves the integration branch exactly
    where it was, and that a phase's own push still succeeds on either side of a
    board commit.
    """
    remote, clients = _git_test_repo(base, "board-branch")
    private = clients[0]
    root = base / "board-root"
    root.mkdir()

    def integration_tip(repo):
        rc, commit, _err = _git(
            repo,
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{_integration_branch(repo, 'origin')}",
        )
        return commit.strip() if rc == 0 else None

    # The store reads its branch from the fixture settings. The board also has
    # to use a branch other than the integration branch, because a reservation
    # cannot leave that branch unchanged when the two branches are the same.
    store = ReservationStore(root, private_repo=private)
    _self_test_check(
        checks,
        "the board uses its configured branch",
        store.branch == SELF_TEST_BRANCH,
        store.branch,
    )
    _self_test_check(
        checks,
        "the board's branch is not the integration branch",
        SELF_TEST_BRANCH != _integration_branch(private, "origin"),
        f"{SELF_TEST_BRANCH} vs {_integration_branch(private, 'origin')}",
    )

    # Reading the board must not be able to change the remote. Seeding used to
    # hang off the fetch, so the first read on a machine that had not migrated
    # published a branch, which is a write on the path that exists precisely so a
    # caller can inspect without writing. The check names both halves: the read
    # refuses, and the remote's branch list is untouched afterwards.
    #
    # The branch is named rather than taken from the store, because the branch a
    # store uses is the configured value, and the fixtures that run this
    # suite deliberately name one that already exists. A probe for "the board
    # branch is missing" has to use a branch that is missing.
    def remote_branches():
        rc, out, _err = _git(remote, "branch", "--format=%(refname:short)")
        return sorted(out.split()) if rc == 0 else None

    probe = ReservationStore(root, private_repo=private, branch="fixture-probe")

    # The seed source is the integration branch's tip, whatever the private
    # checkout is sitting on. A phase run puts that checkout on its own feature
    # branch, so seeding from its HEAD would root every machine's shared board
    # history at whichever commit that phase happened to start from. The fixture
    # makes the two candidates different commits so the check can tell them apart:
    # a fetch-then-resolve answer and a HEAD answer are different commits.
    case = base / "seed-source"
    case.mkdir()
    seed_remote, seed_clients = _git_test_repo(case, "seed-source")
    seed_private = seed_clients[0]
    seed_root = case / "root"
    seed_root.mkdir()
    (seed_private / "integration.txt").write_text("integration\n")
    _git(seed_private, "add", "-A")
    _git(seed_private, "commit", "-q", "-m", "integration work")
    _git(
        seed_private,
        "push",
        "-q",
        "origin",
        f"HEAD:refs/heads/{_integration_branch(seed_private, 'origin')}",
    )
    integration_tip_sha = _git(
        seed_remote,
        "rev-parse",
        f"refs/heads/{_integration_branch(seed_private, 'origin')}",
    )[1].strip()
    _git(seed_private, "checkout", "-q", "-b", "phase-100712-feature")
    (seed_private / "feature.txt").write_text("feature\n")
    _git(seed_private, "add", "-A")
    _git(seed_private, "commit", "-q", "-m", "feature work")
    feature_tip = _git(seed_private, "rev-parse", "HEAD")[1].strip()
    # The stale remote-tracking ref is deleted, because it is what makes the
    # question real: with it present, a `rev-parse` answers the integration tip
    # and no code has to choose anything. A clone that has never fetched the
    # integration branch holds no ref for it, and that is the machine this is
    # about. Fetching before asking is what answers it; asking the checkout is
    # what would answer wrongly.
    _git(
        seed_private,
        "update-ref",
        "-d",
        f"refs/remotes/origin/{_integration_branch(seed_private, 'origin')}",
    )
    seed_store = ReservationStore(
        seed_root, private_repo=seed_private, branch="coordination-seed-source"
    )
    seed_store.migrate_board()
    rc, seeded, _err = _git(
        seed_remote, "rev-parse", "refs/heads/coordination-seed-source"
    )
    seeded_tip = seeded.strip() if rc == 0 else ""
    _self_test_check(
        checks,
        "the board is seeded from the integration tip, not the checkout's branch",
        bool(seeded_tip)
        and seeded_tip == integration_tip_sha
        and integration_tip_sha != feature_tip,
        f"seeded {seeded_tip[:12]}; integration {integration_tip_sha[:12]}; "
        f"on-branch tip {feature_tip[:12]}",
    )
    shutil.rmtree(case, ignore_errors=True)

    before_read = remote_branches()
    refused = ""
    try:
        probe.read()
        refused = "the read succeeded against a remote with no board branch"
    except CoordinationUnavailable as exc:
        refused = str(exc).splitlines()[0][:110]
    after_read = remote_branches()
    _self_test_check(
        checks,
        "a read refuses rather than creating the board branch",
        "does not exist" in refused and before_read == after_read,
        f"{refused}; branches {before_read} -> {after_read}",
    )

    created = probe.migrate_board()
    _self_test_check(
        checks,
        "the named migration step creates the board branch",
        created and remote_branches() != before_read,
        f"created={created}; branches {remote_branches()}",
    )
    _self_test_check(
        checks,
        "migrating twice is the same answer as migrating once",
        probe.migrate_board() is False,
        "a second migration creates nothing",
    )

    before = integration_tip(private)
    record = store.reserve(100710, "server-01")
    after = integration_tip(private)
    _self_test_check(
        checks,
        "a reservation does not move the integration branch",
        before is not None and before == after,
        f"{str(before)[:12]} -> {str(after)[:12]}",
    )

    rc, state, _err = _git(
        private, "show", f"origin/{store.branch}:{state_rel().as_posix()}"
    )
    held = False
    if rc == 0:
        held = (
            json.loads(state).get("phases", {}).get("100710", {}).get("reservation_id")
            == record["reservation_id"]
        )
    _self_test_check(
        checks,
        "the board's own branch holds the reservation",
        held,
        f"origin/{store.branch}",
    )

    # A phase's own commit and a board commit are now siblings on two branches,
    # so neither can be rejected as non-fast-forward by the other. Both orders
    # are exercised, because the race only ever went one way.
    for attempt in ("board-then-phase", "phase-then-board"):
        case = base / f"race-{attempt}"
        case.mkdir()
        _race_remote, race_clients = _git_test_repo(case, attempt)
        race_private = race_clients[0]
        race_root = case / "root"
        race_root.mkdir()
        (race_root / "runs" / "phase-100711").mkdir(parents=True)
        (race_private / "runs" / "phase-100711").mkdir(parents=True)
        phase_store = ReservationStore(
            race_root, private_repo=race_private, branch=SELF_TEST_BRANCH
        )
        integration = _integration_branch(race_private, "origin")

        def publish_run_state():
            # Run state is published from the private checkout, which is where
            # the publishing tool writes it.
            (race_private / "runs" / "phase-100711" / "run.json").write_text(
                json.dumps({"state": "completed"}) + "\n"
            )
            _git(race_private, "add", "-A")
            _git(race_private, "commit", "-q", "-m", "run state")
            return (
                _git(race_private, "push", "origin", f"HEAD:refs/heads/{integration}")[
                    0
                ]
                == 0
            )

        if attempt == "board-then-phase":
            phase_store.reserve(100711, "server-01")
            phase_ok = publish_run_state()
        else:
            phase_ok = publish_run_state()
            phase_store.reserve(100711, "server-01")
        _self_test_check(
            checks,
            f"a phase's own push survives a board commit ({attempt})",
            phase_ok,
            f"pushed to {integration}: {phase_ok}",
        )
        shutil.rmtree(case, ignore_errors=True)


def _deleted_state_check(base):
    remote, clients = _git_test_repo(base, "deleted-state")
    root = base / "deleted-root"
    root.mkdir()
    store = ReservationStore(root, private_repo=clients[0], branch=SELF_TEST_BRANCH)
    store.reserve(100440, "server-01")
    deleter = base / "deleted-client"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(deleter)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(deleter, "config", "user.email", "coordination-test@example.com")
    _git(deleter, "config", "user.name", "coordination test")
    # The deletion has to happen on the board's own branch. Deleting the file on
    # the integration branch used to be the same thing, and is now a change to a
    # file nothing reads.
    _git(deleter, "checkout", "-q", "-B", store.branch, f"origin/{store.branch}")
    _git(deleter, "rm", "-q", state_rel().as_posix())
    _git(deleter, "commit", "-q", "-m", "delete coordination state")
    _git(deleter, "push", "origin", f"HEAD:refs/heads/{store.branch}")
    try:
        store.read()
        return False, "deleted state was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


def _bad_phase_key_check(base):
    bad = {
        "version": 1,
        "phases": {
            "1": {
                "status": "running",
                "machine_id": "server-01",
                "reservation_id": "rsv-bad-key",
                "generation": 1,
                "claimed_at": "x",
                "heartbeat_at": "x",
                "expires_at": None,
                "run_id": "phase-1",
                "base_revisions": {},
            }
        },
    }
    remote, clients = _git_test_repo(base, "bad-key", initial_state=bad)
    root = base / "bad-key-root"
    root.mkdir()
    store = ReservationStore(root, private_repo=clients[0])
    try:
        store.read()
        return False, "noncanonical phase key was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


def _self_test_receipt(store, reservation):
    _git(store.private, "fetch", "origin", "master")
    _git(store.private, "merge", "--ff-only", "origin/master")
    path = store.private / "runs" / f"phase-{reservation['phase']}" / PUBLICATION_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "phase": reservation["phase"],
        "state": "published",
        "reservation": {
            "machine_id": reservation["machine_id"],
            "reservation_id": reservation["reservation_id"],
            "generation": reservation["generation"],
        },
        "public_commit": "0" * 40,
        "private_commit": _head(store.private),
        "published_at": _now(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    _git(store.private, "add", "--", path.relative_to(store.private).as_posix())
    _git(store.private, "commit", "-q", "-m", "test publication receipt")
    _git(store.private, "push", "origin", "HEAD:master")
    return path


def _pushurl_boundary_check(base):
    public_remote = base / "public-boundary.git"
    private_remote = base / "private-boundary.git"
    for remote in (public_remote, private_remote):
        subprocess.run(
            [git_binary(), "init", "--bare", "-q", str(remote)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/master")
    public = base / "public-boundary"
    private = base / "private-boundary"
    subprocess.run(
        [git_binary(), "clone", "-q", str(public_remote), str(public)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(private)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(private, "remote", "set-url", "--push", "origin", str(public_remote))
    root = public
    try:
        ReservationStore(root, private_repo=private)
        return False, "public pushurl was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


def _publication_lock_check(store, reservation, contender):
    store.acquire_publication(reservation)
    try:
        store.acquire_publication(contender)
        blocked = False
    except PublicationBusy:
        blocked = True
    store.release_publication(reservation)
    return blocked


def self_test():
    checks = self_test_checks()
    ok = all(item["ok"] for item in checks)
    # The named FAIL lines and the verdict go to stderr and the JSON record to
    # stdout, so a harness can look for a named failure without the machine
    # record changing shape for its own callers.
    for item in checks:
        if not item["ok"]:
            print(f"FAIL: {item['check']} ({item['detail']})", file=sys.stderr)
    print(f"coordination self-test: {'pass' if ok else 'FAIL'}", file=sys.stderr)
    _print_json({"coordination_self_test": "pass" if ok else "fail", "checks": checks})
    return 0 if ok else 1


def _add_store_args(parser):
    parser.add_argument("--config")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")


def _add_reservation_args(parser):
    parser.add_argument("--phase", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--generation", required=True, type=int)


def _add_token_args(parser):
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--generation", required=True, type=int)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shared phase reservations")
    sub = parser.add_subparsers(dest="command", required=False)

    reserve = sub.add_parser("reserve", help="claim an available phase")
    _add_store_args(reserve)
    reserve.add_argument("--phase", required=True)
    reserve.add_argument("--machine-id", required=True)
    reserve.add_argument("--run-id", default=None)
    reserve.add_argument("--status", choices=sorted(ACTIVE_STATUSES), default="claimed")

    for name, help_text in (
        ("renew", "renew an owned reservation"),
        ("assert-owner", "verify the three-part fence"),
        ("set-status", "change an owned reservation"),
    ):
        command = sub.add_parser(name, help=help_text)
        _add_store_args(command)
        _add_reservation_args(command)
        if name == "set-status":
            command.add_argument("--status", required=True, choices=sorted(STATUSES))
            command.add_argument(
                "--publication-receipt",
                help="published receipt path; required for completed",
            )
            command.add_argument(
                "--accept-reason",
                help="why an operator closed the phase without a passing "
                "checker; required for accepted, and recorded on the board",
            )

    takeover = sub.add_parser("takeover", help="explicitly take over a live claim")
    _add_store_args(takeover)
    takeover.add_argument("--phase", required=True)
    takeover.add_argument("--machine-id", required=True)
    takeover.add_argument("--expected-reservation-id", required=True)
    takeover.add_argument("--expected-generation", required=True, type=int)
    takeover.add_argument("--confirm", action="store_true")

    register = sub.add_parser(
        "register-running", help="register a pre-coordination running phase"
    )
    _add_store_args(register)
    register.add_argument("--phase", required=True)
    register.add_argument("--machine-id", required=True)
    register.add_argument("--run-id", default=None)
    register.add_argument("--confirm", action="store_true")

    release = sub.add_parser(
        "release-publication", help="explicitly release a stale publication lock"
    )
    _add_store_args(release)
    _add_reservation_args(release)
    release.add_argument("--confirm", action="store_true")

    release_group = sub.add_parser(
        "release-group",
        help="give one owner's whole group back at once, with no cooldown",
    )
    _add_store_args(release_group)
    _add_token_args(release_group)

    renew_group = sub.add_parser(
        "renew-group", help="renew every member of one owned group at once"
    )
    _add_store_args(renew_group)
    _add_token_args(renew_group)
    renew_group.add_argument("--status", choices=sorted(ACTIVE_STATUSES))

    expire = sub.add_parser(
        "expire-stale", help="return every stale group to the queue under cooldown"
    )
    _add_store_args(expire)

    show = sub.add_parser("show", help="fetch and print the coordination state")
    _add_store_args(show)
    sub.add_parser("self-test", help="run hermetic two-client checks")
    parser.add_argument(
        "--self-test",
        dest="legacy_self_test",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args(argv)
    if not args.command and not args.legacy_self_test:
        parser.error("a command is required")
    if args.legacy_self_test or args.command == "self-test":
        return self_test()
    try:
        conventions.configure_from_args(args)
        store = _cli_store(args)
        if args.command == "reserve":
            result = store.reserve(
                args.phase, args.machine_id, run_id=args.run_id, status=args.status
            )
        elif args.command in {"renew", "assert-owner", "set-status"}:
            reservation = {
                "phase": _phase_key(args.phase)[0],
                "machine_id": args.machine_id,
                "reservation_id": args.reservation_id,
                "generation": args.generation,
            }
            if args.command == "renew":
                result = store.renew(reservation)
            elif args.command == "assert-owner":
                result = store.assert_owner(reservation)
            else:
                result = store.set_status(
                    reservation,
                    args.status,
                    evidence_path=args.publication_receipt,
                    accepted_reason=args.accept_reason,
                )
        elif args.command == "takeover":
            result = store.takeover(
                args.phase,
                args.machine_id,
                confirm=args.confirm,
                expected_reservation_id=args.expected_reservation_id,
                expected_generation=args.expected_generation,
            )
            save_reservation_file(
                reservation_file(conventions.setting("repo-root"), args.phase), result
            )
        elif args.command == "register-running":
            result = store.register_running(
                args.phase, args.machine_id, run_id=args.run_id, confirm=args.confirm
            )
            save_reservation_file(
                reservation_file(conventions.setting("repo-root"), args.phase), result
            )
        elif args.command == "release-publication":
            if not args.confirm:
                raise CoordinationError(
                    "releasing a publication lock requires confirm=true"
                )
            result = store.release_publication(
                {
                    "phase": _phase_key(args.phase)[0],
                    "machine_id": args.machine_id,
                    "reservation_id": args.reservation_id,
                    "generation": args.generation,
                }
            )
        elif args.command == "release-group":
            result = store.release_group(
                {
                    "machine_id": args.machine_id,
                    "reservation_id": args.reservation_id,
                    "generation": args.generation,
                }
            )
        elif args.command == "renew-group":
            result = store.renew_group(
                {
                    "machine_id": args.machine_id,
                    "reservation_id": args.reservation_id,
                    "generation": args.generation,
                },
                args.status,
            )
        elif args.command == "expire-stale":
            result = store.expire_stale()
        else:
            result, _commit = store.read()
        _print_json(
            {"state": "OK", "reservation": result}
            if args.command not in {"show"}
            else result
        )
        return 0
    except ReservationConflict as exc:
        _print_json(
            {
                "state": "WAIT_FOR_CLAIM",
                "phase": exc.phase,
                "owner": exc.record.get("machine_id"),
                "status": exc.record.get("status"),
                "error": str(exc),
            }
        )
        return 3
    except ReservationFenced as exc:
        _print_json({"state": "FENCED", "error": str(exc)})
        return 4
    except (ConfigError, CoordinationUnavailable, CoordinationError) as exc:
        _print_json({"state": "COORDINATION_UNAVAILABLE", "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
