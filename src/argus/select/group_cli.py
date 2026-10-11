# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The command line for the reservation-group request program.

The request, the release and the renew stay in ``select/group.py``; this module
is only the arguments and the exit code. ``select/group.py`` remains the
program, and its ``__main__`` block calls ``main`` from here.
"""

import argparse
import json
import sys
from pathlib import Path

from argus import conventions
from argus.config.loader import ConfigError
from argus.select import group
from argus.select.group import _release, _renew, _request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=group.__doc__)
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        # Imported here so the program does not carry its own test fixtures, and
        # so `--self-test` still runs the checks the Makefile and the fault
        # harness expect from this file.
        from argus.select.group_selftest import self_test

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
