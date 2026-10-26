# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``scan`` subcommand: its arguments, input check, and dispatch."""

import argparse
from collections.abc import Callable
from typing import Any

from argus import conventions
from argus.board.core import CoordinationError
from argus.board.scan import scan_claims


def add_scan_parser(
    sub: Any, add_store_args: Callable[[argparse.ArgumentParser], None]
) -> None:
    scan = sub.add_parser("scan", help="report run work that no live claim covers")
    add_store_args(scan)
    scan.add_argument("--phase", help="scan only this phase")
    scan.add_argument(
        "--machine-id",
        help="treat a claim held by another machine as uncovered",
    )


def check_scan_args(args: argparse.Namespace) -> None:
    """Refuse an empty filter before the board read, as ``show`` does."""
    if args.machine_id is not None and not args.machine_id.strip():
        raise CoordinationError("--machine-id must not be empty")
    if args.phase is not None and not args.phase.strip():
        raise CoordinationError("--phase must not be empty")


def scan_command(args: argparse.Namespace, store: Any) -> dict[str, Any]:
    return scan_claims(
        conventions.setting("repo-root"),
        store,
        phase=None if args.phase is None else args.phase.strip(),
        machine_id=None if args.machine_id is None else args.machine_id.strip(),
    )
