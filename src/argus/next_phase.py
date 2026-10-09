#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Pick the next piece of work for the driver.

This file is the entry point the driver calls. It resolves the configured work
source and asks it what to run next. It knows nothing about phases, roadmaps,
labels or issues: those belong to the source that answers. The roadmap source
is the only source today and lives in roadmap_source.py; the contract it
satisfies, and the registry of sources, live in work_source.py.

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
  BLOCKED_ON_GATE             the plan owes the owner a decision
  COOLDOWN                    an expired claim is still in its cooldown window
  GROUP_UNAVAILABLE           a declared group could not be resolved
  COORDINATION_INCONSISTENT   the board and the evidence disagree
  COORDINATION_UNAVAILABLE    the board could not be reached

Exit codes: 0 for RESERVED and AVAILABLE, 1 for NO_PHASE, 3 for
WAIT_FOR_CLAIM, 4 for BLOCKED_ON_GATE, 2 for everything else. A result may
carry ``awaiting_review``: items skipped because a pull request of theirs is
open and unmerged. They are finished work rather than queue work, and the
driver runs the merge check on them. A result for a phase that declares a
reservation group also carries ``group`` and ``members``: the whole group in
declaration order and the reservation records claimed for it, all sharing one
reservation id.

``--resolve-group PHASE`` is a read-only query, not a selection. It prints the
reservation group the phase belongs to and the members still unfinished, and it
claims nothing. It is the seam the group request program will use.

``--self-test`` exercises the source, the registry and this file's own wiring,
including the fail-closed paths, and needs neither the network nor a real
board.
"""

import argparse
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus import conventions
from argus.config import ConfigError

# Re-exported rather than moved out from under one caller: the publishing tool's
# self-test imports publication_receipt_done from this module by name.
from argus.phase_reservations import (  # noqa: E402 - path set just above
    CoordinationError,
    ReservationStore,
)
from argus.roadmap_source import (  # noqa: E402,F401 - path set just above
    publication_receipt_done,
    reported_path,
    resolve_group,
)
from argus.roadmap_source import (
    self_test as roadmap_source_self_test,
)
from argus.work_source import (  # noqa: E402 - path set just above
    WorkSourceError,
    resolve,
)
from argus.work_source import (
    self_test as work_source_self_test,
)

#: Exit code per selection state. Anything unlisted is an error, so a new
#: state cannot be mistaken for a launch.
STATE_EXIT = {
    "RESERVED": 0,
    "AVAILABLE": 0,
    "NO_PHASE": 1,
    "WAIT_FOR_CLAIM": 3,
    "BLOCKED_ON_GATE": 4,
    "COOLDOWN": 2,
    "GROUP_UNAVAILABLE": 2,
    "COORDINATION_INCONSISTENT": 2,
}


def self_test():
    roadmap_source_self_test()
    work_source_self_test()
    _self_test_configuration()
    _self_test_group_query()
    print("next_phase self-test: pass")


def _self_test_configuration():
    """This file's own wiring: which source is chosen, and what a bad one does.

    The source's rules are tested where they live. What is tested here is the
    seam between the driver's command line and the source it reaches, because a
    machine that named a folder that does not exist must stop rather than read
    the one that does.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Everything lives under this one temporary directory, and the working
        # directory the test chdirs into is inside it too. A fixture that
        # reached outside would collide with the previous run's leftovers in
        # the shared temporary parent and fail on the second run rather than
        # on the first, which is how this passed once and then did not.
        repo = Path(tmp) / "repo"
        repo.mkdir()
        elsewhere = Path(tmp) / "elsewhere"
        elsewhere.mkdir()
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        (repo / "roadmap" / "phase-100440-first.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )

        # Unconfigured is the roadmap source reading the derived folder, which
        # is what the source returns when the caller gives it a folder.
        default = resolve(None, repo, repo / "roadmap")
        assert default.name == "roadmap", default.name
        assert default.roadmap == repo / "roadmap", default.roadmap

        # Named explicitly, the same source is chosen.
        assert resolve("roadmap", repo, repo / "roadmap").name == "roadmap"

        # A folder inside the repository is read instead of the derived one.
        named = repo / "named"
        named.mkdir()
        (named / "phase-100450-named.md").write_text("x\n")
        assert resolve("roadmap", repo, named).roadmap == named

        # A relative configured folder resolves against the repository root and
        # not the working directory. Cron starts the driver from an arbitrary
        # directory, and the driver's own shell anchors the same way, so the two
        # must never disagree about which folder a name refers to.
        os.chdir(elsewhere)
        try:
            resolved = resolve("roadmap", repo, "named")
        finally:
            os.chdir(repo)
        assert resolved.roadmap == named, resolved.roadmap

        # A name nobody recognises is refused, and the refusal names what is
        # available rather than falling back to a source that would work.
        try:
            resolve("issues", repo)
            refused = False
        except WorkSourceError as exc:
            refused = True
            assert "roadmap" in str(exc), exc
        assert refused, "an unknown work source must not fall back"

        # A folder that cannot be read stops the tick, with the folder named.
        try:
            resolve("roadmap", repo, repo / "absent")
            refused = False
        except WorkSourceError as exc:
            refused = True
            assert "absent" in str(exc), exc
        assert refused, "an unreadable roadmap folder must not fall back"

    # main() turns both refusals into exit 2 with a message on stderr and
    # nothing on stdout, so a caller reading the result sees an error and not
    # an empty selection. Output is captured: the self-test must not add
    # lines to the --check report that runs it.
    with tempfile.TemporaryDirectory() as tmp:
        for argv, expected in (
            (["--repo", tmp, "--source", "issues"], "unknown work source"),
            (
                ["--repo", tmp, "--roadmap", str(Path(tmp) / "absent")],
                "configured roadmap folder",
            ),
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    argv,
                    _settings={
                        "repo-root": Path(tmp),
                        "pipeline": None,
                        "roadmap-dir": Path(tmp) / "roadmap",
                        "driver.include": ["phase-*.md"],
                        "driver.exclude": [],
                        "driver.runs": Path(tmp) / "runs",
                        "machine.id": "self-test",
                    },
                )
            assert code == 2, (argv, code)
            assert expected in err.getvalue(), (argv, err.getvalue())
            assert out.getvalue() == "", (argv, out.getvalue())

    # Repeated --phase-glob flags must reach the source as one include list,
    # and an excluded name must not be listed. The driver passes one flag per
    # configured include, so accepting only the last value would silently drop
    # every other glob.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "phase-100460-first.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        (repo / "roadmap" / "work-100461-renamed.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        (repo / "roadmap" / "work-100462-skip.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                [
                    "--repo",
                    str(repo),
                    "--source",
                    "roadmap",
                    "--roadmap",
                    str(repo / "roadmap"),
                    "--verbose",
                    "--phase-glob",
                    "phase-*.md",
                    "--phase-glob",
                    "work-*.md",
                ],
                _settings={
                    "repo-root": repo,
                    "pipeline": None,
                    "roadmap-dir": repo / "roadmap",
                    "driver.include": ["ignored-*.md"],
                    "driver.exclude": ["work-100462-skip.md"],
                    "driver.runs": repo / "runs",
                    "machine.id": "self-test",
                },
            )
        assert code == 0, (code, err.getvalue())
        assert "phase-100460-first.md" in err.getvalue(), err.getvalue()
        assert "work-100461-renamed.md" in err.getvalue(), err.getvalue()
        assert "work-100462-skip.md" not in err.getvalue(), err.getvalue()


def _self_test_group_query():
    """The read-only group query answers from the roadmap and needs no board.

    The settings below name no board at all. That absence is the proof that
    matters: if the query reached the board it would fail on the missing
    configuration, so a clean answer shows the read path never touches it. The
    second case proves a phase with no declaration is refused by name.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        roadmap = repo / "roadmap"
        roadmap.mkdir()
        for number in (100010, 100020, 100030):
            (roadmap / f"phase-{number}-x.md").write_text(
                "### Reservation Group\n\n`together: 100010, 100020, 100030`\n"
            )
        (roadmap / "phase-100040-y.md").write_text("## 1. Objective\n")
        rundir = repo / "runs" / "phase-100010"
        rundir.mkdir(parents=True)
        (rundir / "run.json").write_text(json.dumps({"state": "completed"}))
        settings = {
            "repo-root": repo,
            "pipeline": None,
            "roadmap-dir": roadmap,
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": repo / "runs",
            "machine.id": "self-test",
        }
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--resolve-group", "100020"], _settings=settings)
        assert code == 0, (code, out.getvalue(), err.getvalue())
        result = json.loads(out.getvalue())
        assert result["state"] == "GROUP", result
        assert result["group"] == ["100010", "100020", "100030"], result
        assert result["remainder"] == ["100020", "100030"], result
        assert not (repo / "vault").exists(), (
            "the group query must not create a board checkout"
        )

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--resolve-group", "100040"], _settings=settings)
        assert code == 2, (code, out.getvalue(), err.getvalue())
        result = json.loads(out.getvalue())
        assert result["state"] == "GROUP_UNAVAILABLE", result
        assert "no '### Reservation Group' section" in result["error"], result
        assert result["document"].endswith("phase-100040-y.md"), result


def main(argv=None, *, _settings=None):
    parser = argparse.ArgumentParser(
        description="Next piece of work, from the configured work source."
    )
    parser.add_argument("--repo-root", "--repo", dest="config_repo_root")
    parser.add_argument(
        "--source", default=None, help="work source to ask (default: roadmap)"
    )
    parser.add_argument(
        "--phase-glob",
        dest="config_phase_glob",
        action="append",
        help="work-item file glob (repeatable)",
    )
    parser.add_argument("--roadmap-dir", "--roadmap", dest="config_roadmap_dir")
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
            if args.config_roadmap_dir is not None:
                settings["roadmap-dir"] = Path(args.config_roadmap_dir)
            if args.config_phase_glob is not None:
                settings["driver.include"] = list(args.config_phase_glob)
            conventions.configure(settings)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    repo = Path(conventions.setting("repo-root")).resolve()
    roadmap = conventions.setting("roadmap-dir")
    # The group query is read-only and comes before anything that writes. It
    # resolves the group from the phase documents and claims nothing, so it
    # needs neither the board nor the work-source registry.
    if args.resolve_group is not None:
        try:
            result = resolve_group(repo, roadmap, args.resolve_group)
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
    # Before the roadmap is resolved, because this step needs the repository and
    # nothing else. A machine can migrate its coordination branch even while its
    # roadmap folder is unreadable, and the two failures are unrelated.
    if args.migrate_board:
        try:
            created = ReservationStore(repo).migrate_board()
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
        source = resolve(args.source, repo, roadmap)
    except WorkSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.server:
        try:
            result = source.select(
                args.machine_id or conventions.setting("machine.id"),
                reserve=not args.read_only,
            )
        except CoordinationError as exc:
            result = {"state": "COORDINATION_UNAVAILABLE", "error": str(exc)}
            print(json.dumps(result, sort_keys=True))
            return 2
        print(json.dumps(result, sort_keys=True))
        return STATE_EXIT.get(result["state"], 2)
    if args.audit:
        return 0 if source.audit(apply_index=args.fix_index) == 0 else 1
    if args.verbose:
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
