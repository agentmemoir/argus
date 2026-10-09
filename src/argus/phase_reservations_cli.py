# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The command line: arguments, dispatch, and the JSON result."""

import argparse

from argus import conventions
from argus.config import ConfigError
from argus.phase_reservations_core import (
    ACTIVE_STATUSES,
    STATUSES,
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationFenced,
    _phase_key,
    _print_json,
)
from argus.phase_reservations_files import reservation_file, save_reservation_file
from argus.phase_reservations_selftest import self_test
from argus.phase_reservations_store import ReservationStore


def _cli_store(_args):
    return ReservationStore(conventions.setting("repo-root"))


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
