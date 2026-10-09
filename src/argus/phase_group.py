#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Request a reservation group for one agent and claim it on the shared board.

An agent identifies itself by its machine id and names one phase. The phase
documents declare the group, so the request resolves the group from the
roadmap, drops the members that are already completed, and claims the
unfinished remainder in one board write under one shared reservation id. The
answer is the array of the members it now owns; there is no separate accept
step, because the request is the claim.

Every refusal fails closed and reserves nothing:

* a group another agent holds names the holding agent;
* a group whose every member is completed fails with ``already completed``;
* a group whose documents carry no declaration names the document;
* a request during the cooldown window reports the time left.

Usage:
    python3 phase_group.py --phase 100420 --machine-id macbook [--config FILE]
    python3 phase_group.py --release --phase 100420
    python3 phase_group.py --renew --phase 100420
    python3 phase_group.py --self-test

The request is the claim. ``--release`` and ``--renew`` act on the reservation
this machine already holds and read the token a request or the driver wrote
beside the phase, so they can be run for a group claimed before a launch. A
request makes two board writes, the expiry sweep and then the claim, so a group
that already returned to the queue refuses on its cooldown rather than reading
as live.

Output: on success, a JSON array of the claimed members, each carrying the
shared ``reservation_id``. A release or renew answers an object with ``state``
and the members it moved. On refusal, a JSON object with ``state`` and
``error``. Exit 0 on success, 1 for an all-completed group, 2 for a group that
cannot be resolved or a board failure, 3 for a group held elsewhere, 4 for a
token that is fenced or expired, and 6 for a cooldown.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus import conventions
from argus.config import ConfigError
from argus.phase_reservations import (  # noqa: E402 - path set just above
    ACTIVE_STATUSES,
    CoordinationError,
    ReservationConflict,
    ReservationCooldown,
    ReservationFenced,
    ReservationStore,
    load_reservation_file,
    reservation_file,
    save_reservation_file,
)
from argus.roadmap_source import resolve_group
from argus.work_source import WorkSourceError

#: Exit code per request state. Anything unlisted is an error, so a new state
#: cannot be mistaken for a successful claim.
REQUEST_EXIT = {
    "RESERVED": 0,
    "RELEASED": 0,
    "RENEWED": 0,
    "ALREADY_COMPLETED": 1,
    "GROUP_UNAVAILABLE": 2,
    "COORDINATION_UNAVAILABLE": 2,
    "WAIT_FOR_CLAIM": 3,
    "FENCED": 4,
    "COOLDOWN": 6,
}


def _held_group(store, key, remainder, machine_id):
    """The reservation this machine already holds over ``remainder``, or ``None``.

    Returns the keyword arguments that re-claim the same group: one shared
    reservation id, its generation, and the status the hold already carries, so a
    refresh cannot quietly change the window the claim is measured by. Only an
    exact hold of the whole remainder takes this path, so no member of the group
    is left behind and no second reservation id is minted for one group.
    """
    try:
        state, _commit = store.read()
    except CoordinationError:
        return None
    records = state.get("phases", {})
    mine = records.get(key) or {}
    if (
        mine.get("machine_id") != machine_id
        or mine.get("status") not in ACTIVE_STATUSES
    ):
        return None
    reservation_id = mine.get("reservation_id")
    if not reservation_id:
        return None
    if any(
        (records.get(member) or {}).get("reservation_id") != reservation_id
        for member in remainder
    ):
        return None
    return {
        "reservation_id": reservation_id,
        "generation": mine.get("generation"),
        "status": mine.get("status"),
    }


def _request(repo, roadmap, phase, machine_id, *, store=None):
    """Resolve ``phase`` to its group and claim the unfinished remainder.

    Returns ``(result, exit_code)``. On success ``result`` is the list of
    per-member reservation records, all sharing one reservation id. On any
    refusal it is a JSON-ready dict, and nothing is reserved. The group is
    read from the phase documents through the read-side resolver, so a group
    with no declaration is named rather than guessed.
    """
    try:
        group = resolve_group(repo, roadmap, phase)
    except WorkSourceError as exc:
        result = {
            "state": "GROUP_UNAVAILABLE",
            "phase": str(phase),
            "error": str(exc),
        }
        document = getattr(exc, "document", None)
        if document is not None:
            result["document"] = str(document)
        return result, REQUEST_EXIT["GROUP_UNAVAILABLE"]
    key = group["phase"]
    remainder = list(group["remainder"])
    if not remainder:
        return {
            "state": "ALREADY_COMPLETED",
            "phase": key,
            "group": list(group["group"]),
        }, REQUEST_EXIT["ALREADY_COMPLETED"]
    try:
        store = store or ReservationStore(repo)
        # A request from the machine that already holds the whole group is the
        # same claim, so answer the hold it has rather than refusing the owner
        # its own reservation. The store's idempotency only covers an identical
        # reservation id, and this program mints a fresh one per request.
        held = _held_group(store, key, remainder, machine_id)
        records = store.reserve_group(remainder, machine_id, **(held or {}))
        for record in records:
            try:
                save_reservation_file(reservation_file(repo, record["phase"]), record)
            except OSError as exc:
                raise CoordinationError(
                    f"could not persist phase reservation token: {exc}"
                ) from exc
    except ReservationCooldown as exc:
        return {
            "state": "COOLDOWN",
            "phase": exc.phase,
            "remaining_seconds": exc.remaining_seconds,
            "error": str(exc),
        }, REQUEST_EXIT["COOLDOWN"]
    except ReservationConflict as exc:
        return {
            "state": "WAIT_FOR_CLAIM",
            "phase": exc.phase,
            "owner": exc.record.get("machine_id"),
            "status": exc.record.get("status"),
            "reservation_id": exc.record.get("reservation_id"),
            "generation": exc.record.get("generation"),
            "error": str(exc),
        }, REQUEST_EXIT["WAIT_FOR_CLAIM"]
    except CoordinationError as exc:
        return {
            "state": "COORDINATION_UNAVAILABLE",
            "phase": key,
            "error": str(exc),
        }, REQUEST_EXIT["COORDINATION_UNAVAILABLE"]
    return records, REQUEST_EXIT["RESERVED"]


def _held_token(repo, phase, args):
    """The fence a release or renew acts on, or raise a clear error.

    The token beside the phase is the fence a request or the driver wrote, so a
    group claimed by a selector that never launched can still be given back.
    Explicit flags win, which is what rescues a machine whose token is gone.
    """
    key = f"{int(phase):06d}"
    token = load_reservation_file(reservation_file(repo, key)) or {}
    machine_id = args.machine_id or token.get("machine_id")
    reservation_id = args.reservation_id or token.get("reservation_id")
    generation = (
        args.generation if args.generation is not None else token.get("generation")
    )
    if not machine_id or not reservation_id or generation is None:
        raise CoordinationError(
            f"phase {key} has no reservation token; pass --machine-id, "
            "--reservation-id and --generation, or request the group first"
        )
    return {
        "machine_id": machine_id,
        "reservation_id": reservation_id,
        "generation": generation,
    }


def _release(repo, phase, args, *, store=None):
    """Give this machine's whole group back, and report the members it returned."""
    key = f"{int(phase):06d}"
    store = store or ReservationStore(repo)
    try:
        reservation = _held_token(repo, key, args)
        released = store.release_group(reservation)
    except ReservationFenced as exc:
        return {"state": "FENCED", "phase": key, "error": str(exc)}, REQUEST_EXIT[
            "FENCED"
        ]
    except CoordinationError as exc:
        return {
            "state": "COORDINATION_UNAVAILABLE",
            "phase": key,
            "error": str(exc),
        }, REQUEST_EXIT["COORDINATION_UNAVAILABLE"]
    return {"state": "RELEASED", "phase": key, "released": released}, REQUEST_EXIT[
        "RELEASED"
    ]


def _renew(repo, phase, args, *, store=None):
    """Refresh this machine's whole group, and report the members it moved."""
    key = f"{int(phase):06d}"
    store = store or ReservationStore(repo)
    try:
        reservation = _held_token(repo, key, args)
        records = store.renew_group(reservation, args.status)
    except ReservationFenced as exc:
        return {"state": "FENCED", "phase": key, "error": str(exc)}, REQUEST_EXIT[
            "FENCED"
        ]
    except CoordinationError as exc:
        return {
            "state": "COORDINATION_UNAVAILABLE",
            "phase": key,
            "error": str(exc),
        }, REQUEST_EXIT["COORDINATION_UNAVAILABLE"]
    return {
        "state": "RENEWED",
        "phase": key,
        "members": [record["phase"] for record in records],
    }, REQUEST_EXIT["RENEWED"]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        default=None,
        help="one phase in the group to request, release or renew",
    )
    parser.add_argument(
        "--machine-id", default=None, help="the requesting agent's machine id"
    )
    parser.add_argument(
        "--release", action="store_true", help="give this machine's whole group back"
    )
    parser.add_argument(
        "--renew", action="store_true", help="refresh this machine's whole group"
    )
    parser.add_argument(
        "--status", default=None, help="the status a renewal sets, for --renew"
    )
    parser.add_argument(
        "--reservation-id",
        dest="reservation_id",
        default=None,
        help="the fence to act on when no token is present",
    )
    parser.add_argument(
        "--generation",
        type=int,
        default=None,
        help="the fence generation when no token is present",
    )
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--config")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.self_test:
        # Imported here so the program does not carry its own test fixtures, and
        # so `--self-test` still runs the checks the Makefile and the fault
        # harness expect from this file.
        from argus.phase_group_selftest import self_test

        return self_test()
    if not args.phase:
        print("phase_group: --phase is required", file=sys.stderr)
        return 2
    if not (args.release or args.renew) and not args.machine_id:
        print("phase_group: --machine-id is required for a request", file=sys.stderr)
        return 2
    try:
        phase = f"{int(args.phase):06d}"
    except (TypeError, ValueError):
        print("phase_group: --phase must be a phase number", file=sys.stderr)
        return 2
    try:
        conventions.configure_from_args(args)
    except ConfigError as exc:
        print(f"phase_group: {exc}", file=sys.stderr)
        return 2
    repo = Path(conventions.setting("repo-root")).resolve()
    roadmap = conventions.setting("roadmap-dir")
    if args.release:
        result, code = _release(repo, phase, args)
    elif args.renew:
        result, code = _renew(repo, phase, args)
    else:
        result, code = _request(repo, roadmap, phase, args.machine_id)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
