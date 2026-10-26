#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pick the next piece of work for the driver.

This file is the entry point the driver calls. It resolves the configured work
source and asks it what to run next. It knows nothing about phases, labels or
issues: those belong to the source that answers. The phases source
and the issue source are registered in work_source.py, beside the contract
they satisfy.

Run the source tool by path, e.g.:
  python3 /path/to/argus/next_phase.py --repo /path/to/project

WHAT THE DRIVER READS BACK

Prints "<number>\\t<repo-relative path>" for the lowest incomplete item and
exits 0; exits 1 when every item is complete; exits 2 on a usage or state
error. With ``--server`` it first consults the shared reservation board,
reserves the item, and prints a JSON result:

  RESERVED                    claimed for this machine, launch it
  AVAILABLE                   claimable, reported without claiming (--read-only)
  NO_PHASE                    nothing left to do
  WAIT_FOR_CLAIM              owned by another live reservation; do not jump it
  COOLDOWN                    an expired claim is still in its cooldown window
  GROUP_UNAVAILABLE           a declared group could not be resolved
  COORDINATION_INCONSISTENT   the board and the evidence disagree
  COORDINATION_UNAVAILABLE    the board could not be reached

Exit codes: 0 for RESERVED and AVAILABLE, 1 for NO_PHASE, 3 for
WAIT_FOR_CLAIM, 2 for everything else. An open decision gate is reported under
``needs_decision`` and never stops the run. A result may
carry ``awaiting_review``: items skipped because a pull request of theirs is
open and unmerged. They are finished work rather than queue work, and the
driver runs the merge check on them. A result for a phase that declares a
reservation group also carries ``group`` and ``members``: the whole group in
declaration order and the reservation records claimed for it, all sharing one
reservation id.

A result from the issue source always carries ``kind: "issue"``, ``skipped``
(a count per skip reason), ``preference`` and ``truncated`` (true when the
candidate list may have been capped), and ``issue`` (the bare issue number)
when it names an item; it never carries ``phase``, ``group`` or ``members``.
Its open linked pull requests are reported in ``watching`` and never in
``awaiting_review``, so the phase merge check never sees an issue. A result
with no ``kind`` is a phase result.

``--resolve-group PHASE`` is a read-only query, not a selection. It prints the
reservation group the phase belongs to and the members still unfinished, and it
claims nothing. It is the seam the group request program will use.

``--self-test`` exercises the source, the registry and this file's own wiring,
including the fail-closed paths, and needs neither the network nor a real
board.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions

# Re-exported rather than moved out from under one caller: the publishing tool's
# self-test imports publication_receipt_done from this module by name.
from argus.board.reservations import (  # noqa: E402 - path set just above
    CoordinationError,
    ReservationStore,
)
from argus.config.loader import ConfigError
from argus.phases.source import (  # noqa: E402,F401 - path set just above
    publication_receipt_done,
    reported_path,
    resolve_group,
)
from argus.select.work_source import (  # noqa: E402 - path set just above
    WorkSourceError,
    resolve,
)

__all__ = ["publication_receipt_done"]

#: Exit code per selection state. Anything unlisted is an error, so a new
#: state cannot be mistaken for a launch.
STATE_EXIT = {
    "RESERVED": 0,
    "AVAILABLE": 0,
    "NO_PHASE": 1,
    "WAIT_FOR_CLAIM": 3,
    "COOLDOWN": 2,
    "GROUP_UNAVAILABLE": 2,
    "COORDINATION_INCONSISTENT": 2,
}


def main(
    argv: list[str] | None = None, *, _settings: dict[str, Any] | None = None
) -> int:
    parser = argparse.ArgumentParser(
        description="Next piece of work, from the configured work source."
    )
    parser.add_argument("--repo-root", "--repo", dest="config_repo_root")
    parser.add_argument(
        "--source", default=None, help="work source to ask (default: phases)"
    )
    parser.add_argument(
        "--phase-glob",
        dest="config_phase_glob",
        action="append",
        help="work-item file glob (repeatable)",
    )
    parser.add_argument(
        "--phases-directory", "--phases", dest="config_phases_directory"
    )
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--config")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print per-signal evidence for every phase to stderr",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="report index.md drift; exit 0 when clean, 1 when drift is found",
    )
    parser.add_argument(
        "--fix-index",
        action="store_true",
        help="with --audit, annotate index.md rows for phases "
        "that have a completion marker but are not "
        "indexed Complete; a drift in the other "
        "direction is never repaired automatically",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="select and reserve the lowest incomplete phase",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="with --server, inspect claims without writing",
    )
    parser.add_argument(
        "--machine-id", default=None, help="stable host ID used by --server"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --server, print a machine-readable result",
    )
    parser.add_argument(
        "--resolve-group",
        dest="resolve_group",
        metavar="PHASE",
        default=None,
        help="read-only: print the reservation group this "
        "phase belongs to, and the members still "
        "unfinished; claims nothing",
    )
    parser.add_argument(
        "--migrate-board",
        action="store_true",
        help="create the coordination branch if the remote does "
        "not have it yet, then stop. This is the only "
        "named step that creates it, so a caller says out "
        "loud that it means to write",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        # Imported here so the entry file does not carry its own test fixtures,
        # and so --self-test still runs the checks the Makefile and the fault
        # harness expect from this file.
        from argus.select.next_phase_selftest import self_test

        try:
            self_test()
        except Exception as exc:  # noqa: BLE001 - the self-test reports its own failure
            # A verdict line is printed even when a check raises, because the
            # guard harness reads the verdict to tell an undetected fault from
            # a self-test that stopped part way and proved nothing.
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("next_phase self-test: FAIL", file=sys.stderr)
            return 1
        return 0
    try:
        if _settings is None:
            conventions.configure_from_args(args)
        else:
            settings = dict(_settings)
            if args.config_repo_root is not None:
                settings["repo-root"] = Path(args.config_repo_root)
            if args.config_phases_directory is not None:
                settings["phases.directory"] = args.config_phases_directory
            if args.config_phase_glob is not None:
                settings["driver.include"] = list(args.config_phase_glob)
            conventions.configure(settings)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    repo = Path(conventions.setting("repo-root")).resolve()
    phases = conventions.phases_folder(repo)
    # The group query is read-only and comes before anything that writes. It
    # resolves the group from the phase documents and claims nothing, so it
    # needs neither the board nor the work-source registry.
    if args.resolve_group is not None:
        try:
            result = resolve_group(repo, phases, args.resolve_group)
        except WorkSourceError as exc:
            result = {
                "state": "GROUP_UNAVAILABLE",
                "phase": str(args.resolve_group),
                "error": str(exc),
            }
            document = getattr(exc, "document", None)
            if document is not None:
                result["document"] = str(document)
            print(json.dumps(result, sort_keys=True))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    # Before the phases is resolved, because this step needs the repository and
    # nothing else. A machine can migrate its coordination branch even while its
    # phases folder is unreadable, and the two failures are unrelated.
    if args.migrate_board:
        try:
            created = ReservationStore(
                repo, issues=args.source == "issues"
            ).migrate_board()
        except CoordinationError as exc:
            print(
                json.dumps(
                    {"state": "COORDINATION_UNAVAILABLE", "error": str(exc)},
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps({"state": "BOARD_READY", "created": created}, sort_keys=True))
        return 0
    try:
        source = resolve(args.source, repo, phases)
    except WorkSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        return _select(args, source, repo)
    except (WorkSourceError, CoordinationError) as exc:
        if args.server:
            result = {"state": "COORDINATION_UNAVAILABLE", "error": str(exc)}
            if source.name == "issues":
                result["kind"] = "issue"
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


def _select(args: argparse.Namespace, source: Any, repo: Path) -> int:
    if args.server:
        result = source.select(
            args.machine_id or conventions.setting("machine.id"),
            reserve=not args.read_only,
        )
        print(json.dumps(result, sort_keys=True))
        return STATE_EXIT.get(result["state"], 2)
    if args.audit:
        return 0 if source.audit(apply_index=args.fix_index) == 0 else 1
    if args.verbose:
        if source.name == "issues":
            source.verbose()
        else:
            for item in source.candidates():
                s = source.signals(item)
                print(
                    f"{item[0]}\t{item[1].name}\trun={int(s['run'])} "
                    f"ledger={int(s['ledger'])} log={int(s['finalize_log'])} "
                    f"publication={int(s['publication'])} "
                    f"box={int(s['approval'])} index={int(s['index'])} "
                    f"decision={int(s['needs_decision'])}",
                    file=sys.stderr,
                )
    nxt = source.select_next()
    if nxt is None:
        return 1
    number, path = nxt
    print(f"{number}\t{reported_path(repo, path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
