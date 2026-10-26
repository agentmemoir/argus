# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command line: arguments, dispatch, and the JSON result."""

import argparse
from collections.abc import Sequence
from typing import Any, cast

from argus import conventions
from argus.board.cli_scan import add_scan_parser, check_scan_args, scan_command
from argus.board.core import (
    ACTIVE_STATUSES,
    STATUSES,
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationFenced,
    _issue_key,
    _phase_key,
    _print_json,
)
from argus.board.files import reservation_file, save_reservation_file
from argus.board.selftest import self_test
from argus.board.store import ReservationStore
from argus.config.loader import ConfigError


def _cli_store(args: argparse.Namespace) -> ReservationStore:
    return ReservationStore(conventions.setting("repo-root"), issues=args.issues)


def _add_store_args(parser: argparse.ArgumentParser, *, issues: bool = True) -> None:
    parser.add_argument("--config")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--phases-directory", dest="config_phases_directory")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    if issues:
        parser.add_argument(
            "--issues", action="store_true", help="use issue reservations"
        )
    else:
        parser.set_defaults(issues=False)


def _add_reservation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--generation", required=True, type=int)


def _show_filters(args: argparse.Namespace) -> tuple[str | None, list[str] | None]:
    """Return the stripped show filters, refusing an empty one before the read."""
    machine_id = cast("str | None", args.machine_id)
    reservation_ids = cast("list[str] | None", args.reservation_id)
    if machine_id is not None and not machine_id.strip():
        raise CoordinationError("--machine-id must not be empty")
    for asked in reservation_ids or []:
        if not asked.strip():
            raise CoordinationError("--reservation-id must not be empty")
    stripped = (
        None
        if reservation_ids is None
        else [asked.strip() for asked in reservation_ids]
    )
    return (None if machine_id is None else machine_id.strip(), stripped)


def _narrow_state(
    state: dict[str, Any],
    *,
    reservation_ids: Sequence[str] | None = None,
    machine_id: str | None = None,
) -> dict[str, Any]:
    """Return the board view already read, narrowed to the asked-for records.

    ``--reservation-id`` narrows the phase records only. ``--machine-id``
    narrows every record that names a machine, so a live publication lock
    held by another machine goes too and the ``publication`` key is dropped.
    The filters narrow what one read returned; they never start another read,
    so the output keeps the board's shape except for that dropped key. An id
    the board does not hold is simply absent from the narrowed view, not an
    error.
    """
    if reservation_ids is None and machine_id is None:
        return state
    wanted = None if reservation_ids is None else set(reservation_ids)
    phases = {
        key: record
        for key, record in state["phases"].items()
        if (wanted is None or record.get("reservation_id") in wanted)
        and (machine_id is None or record.get("machine_id") == machine_id)
    }
    narrowed = {**state, "phases": phases}
    lock = state.get("publication")
    if (
        machine_id is not None
        and lock is not None
        and lock.get("machine_id") != machine_id
    ):
        del narrowed["publication"]
    return narrowed


def _add_token_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--generation", required=True, type=int)


def main(argv: list[str] | None = None) -> int:
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
        if name == "assert-owner":
            command.add_argument(
                "--no-wait",
                action="store_true",
                help="refuse at once if a fetch holds the board lock",
            )
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
    _add_store_args(release, issues=False)
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
    show.add_argument(
        "--reservation-id",
        dest="reservation_id",
        action="append",
        help="show only this reservation's phase record; repeat for more than one; the publication lock is not narrowed by this flag",
    )
    show.add_argument(
        "--machine-id",
        help="show only the phase records and publication lock this machine holds",
    )
    sub.add_parser("self-test", help="run hermetic two-client checks")

    add_scan_parser(sub, _add_store_args)

    args = parser.parse_args(argv)
    if not args.command:
        parser.error("a command is required")
    if args.command == "self-test":
        return self_test()
    try:
        conventions.configure_from_args(args)
        store = _cli_store(args)
        machine_id: str | None = None
        reservation_ids: list[str] | None = None
        if args.command == "show":
            machine_id, reservation_ids = _show_filters(args)
        if args.command == "scan":
            check_scan_args(args)
        if args.command == "reserve":
            result = store.reserve(
                args.phase, args.machine_id, run_id=args.run_id, status=args.status
            )
        elif args.command in {
            "renew",
            "assert-owner",
            "set-status",
        }:
            reservation = {
                "issue" if args.issues else "phase": (
                    _issue_key(args.phase)[1]
                    if args.issues
                    else _phase_key(args.phase)[0]
                ),
                "machine_id": args.machine_id,
                "reservation_id": args.reservation_id,
                "generation": args.generation,
            }
            if args.command == "renew":
                result = store.renew(reservation)
            elif args.command == "assert-owner":
                result = store.assert_owner(reservation, wait=not args.no_wait)
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
                reservation_file(
                    conventions.setting("repo-root"),
                    args.phase,
                    kind="issue" if args.issues else "phase",
                ),
                result,
            )
        elif args.command == "register-running":
            result = store.register_running(
                args.phase, args.machine_id, run_id=args.run_id, confirm=args.confirm
            )
            save_reservation_file(
                reservation_file(
                    conventions.setting("repo-root"),
                    args.phase,
                    kind="issue" if args.issues else "phase",
                ),
                result,
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
        elif args.command == "scan":
            result = scan_command(args, store)
        else:
            result, _commit = store.read()
        if args.command == "show":
            result = _narrow_state(
                result, reservation_ids=reservation_ids, machine_id=machine_id
            )
        _print_json(
            {"state": "OK", "reservation": result}
            if args.command not in {"show", "scan"}
            else result
        )
        return 0
    except ReservationConflict as exc:
        _print_json(
            {
                "state": "WAIT_FOR_CLAIM",
                "issue" if args.issues else "phase": exc.phase,
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
    raise SystemExit(main())
