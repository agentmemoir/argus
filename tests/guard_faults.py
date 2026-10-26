#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Break each launch guard on purpose and prove a self-test notices.

A guard that nothing tests is a comment. A guard whose test cannot fail is worse
than that, because it reports the guard is covered while the guard is gone. Four
of this driver's guards were in exactly that state at once: the launchability
check sat below the reservation, so a machine configured with a folder it cannot
launch from reserved a phase and then refused, and every later tick read that
claim back as WAIT_FOR_CLAIM and exited 0. The refusal was real, a check
asserted it, and the check was structurally unable to see the claim.

So this harness does the only thing that settles it. Each fault disables
one guard, or moves it, and then the driver's own self-test has to fail with a
named line. A fault whose self-test still passes is reported as uncovered, which
is the finding: the check does not detect the fault it was written for.

Two rules make the result mean something:

* **The fault must be caught, not merely change the output.** A fault that merely
  changes a count, a path or a wording is not detected. Only a named FAIL line
  is, because that is the only thing a self-test emits when it has decided a
  behaviour is wrong.
* **A fault the code refuses to apply is a failure, not a skip.** If a patch no
  longer matches, the guard it targets has been rewritten and the fault needs
  re-aiming. Passing because a fault was silently not applied would turn this
  harness into the thing it exists to replace.

The harness patches a copy of the driver's source files, one copy per fault, and
checks that each copy is restored byte for byte. The checkout is only read, so no
fault can touch a live driver. The copies run side by side, one per CPU. The
self-test creates its own project, board, and run state; project history is not
needed.

The self-test is the right thing to run here because it is hermetic: it builds
its own fixture repositories and its own board, and it writes only under its own
temporary directory. It is not the thing that pushed a claim onto the real board
during the incident this harness exists for — that was a hand-run of the driver,
which is precisely what this does not do.

    python3 tests/guard_faults.py            # run every fault
    python3 tests/guard_faults.py --list     # names and intents
    python3 tests/guard_faults.py --only NAME
    python3 tests/guard_faults.py --order reverse

A driver fault names the self-test cases that catch it, in its ``cases`` key,
so it runs those cases and not the whole twelve-case suite. The Python checks
are already scoped by their check name.

``--order`` runs the same faults in reverse or shuffled order. A guard suite
that only passes in one order is order-dependent, and the fault that failed in
declared order and passed alone is what that costs: the run has to be probeable
for it, not just observed once. The report lists the faults in that order, not
in the order they finished.

Each fault runs in its own copy of the repository, in its own temporary directory,
with its own tmux server. The child process drops all config settings from its
environment, so the fixture must pass its config file to every tool.

Exit status is 1 when any fault went undetected, 2 when a fault could not run or
the driver could not be restored. With --list, 0 always.
"""

import argparse
import os
import random
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from argus.exec_tempdir import make_exec_dir
from faults import FAULTS
from guard_faults_engine import DRIVER, RestoreError, check_fault

#: What a fault's copy leaves out: version control, caches and build output. A
#: fault needs source files only, and the copies stay small. A `target` folder is
#: also where the self-test may put its own scratch, so it must not be copied.
COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "target",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
)


@dataclass(frozen=True)
class Outcome:
    """One fault's verdict. `failure` names a harness problem, not a verdict."""

    name: str
    caught: bool | None
    detail: str
    seconds: float
    failure: str = ""


def print_slowest(timings: list[tuple[float, str]]) -> None:
    """Name the ten slowest faults, slowest first, so a slow one is easy to find."""
    print("fault time: slowest 10")
    for seconds, name in sorted(timings, reverse=True)[:10]:
        print(f"fault time:   {seconds:7.1f} s  {name}")


def run_in_copy(repo: Path, fault: dict[str, Any]) -> Outcome:
    """Run one fault in its own copy of the repository, then discard the copy.

    The copy is made in a scratch directory the host lets programs run from, so the
    self-test that runs against it can start its tools there. The checkout is only
    read, and no two faults share a copy.
    """
    name = str(fault["name"])
    scratch = make_exec_dir()
    if not scratch:
        return Outcome(
            name,
            None,
            "no executable temporary directory on this host",
            0.0,
            "NOT RUN",
        )
    tree = Path(scratch) / "repo"
    try:
        shutil.copytree(repo, tree, ignore=COPY_IGNORE)
    except OSError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        return Outcome(
            name, None, f"could not copy the repository: {exc}", 0.0, "NOT RUN"
        )
    try:
        started = time.monotonic()
        try:
            caught, detail = check_fault(tree, tree / DRIVER, fault)
        except RestoreError as exc:
            elapsed = time.monotonic() - started
            return Outcome(name, None, str(exc), elapsed, "NOT RESTORED")
        return Outcome(name, caught, detail, time.monotonic() - started)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Break each launch guard and prove a self-test notices."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every fault, what it breaks, and what catches it",
    )
    parser.add_argument(
        "--only", action="append", default=None, help="run only this fault; repeatable"
    )
    parser.add_argument(
        "--order",
        choices=("declared", "reverse", "shuffle"),
        default="declared",
        help="fault order; reverse and shuffle show whether a "
        "result depends on which fault ran first",
    )
    parser.add_argument("--seed", type=int, default=0, help="seed for --order shuffle")
    args = parser.parse_args(argv)

    if args.list:
        for fault in FAULTS:
            caught_by = fault.get("expect") or fault.get("expect_absent")
            print(
                f"{fault['name']}\n"
                f"    breaks:  {fault['breaks']}\n"
                f"    file:    {fault.get('file', DRIVER)}\n"
                f"    caught:  {caught_by}"
            )
        return 0

    selected = list(FAULTS)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {fault["name"] for fault in FAULTS}
        if unknown:
            print(
                f"error: no such fault: {', '.join(sorted(unknown))}", file=sys.stderr
            )
            return 2
        selected = [f for f in FAULTS if f["name"] in wanted]

    if args.order == "reverse":
        selected = list(reversed(selected))
    elif args.order == "shuffle":
        random.Random(args.seed).shuffle(selected)

    repo = Path(__file__).resolve().parents[1]
    driver = repo / DRIVER
    if not driver.is_file():
        print(f"error: no driver at {driver}", file=sys.stderr)
        return 2

    workers = os.cpu_count() or 1
    print(
        f"fault injection: {len(selected)} fault(s), order={args.order}, "
        f"each in its own copy of the repository, {workers} at a time"
    )
    missed = 0
    stale = 0
    failures = 0
    timings: list[tuple[float, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # map yields in the chosen order, even when a later fault finishes first.
        for outcome in pool.map(partial(run_in_copy, repo), selected):
            sys.stdout.write(f"  {outcome.name} ... ")
            if outcome.failure:
                failures += 1
                print(outcome.failure)
                print(f"    {outcome.detail}", file=sys.stderr)
                continue
            timings.append((outcome.seconds, outcome.name))
            if outcome.caught is None:
                # Its own line, its own count, and its own sentence in the summary. A
                # stale anchor reads as an uncovered guard otherwise, and the reader
                # goes looking for a defect in the driver that is not there.
                stale += 1
                print(f"NOT APPLICABLE ({outcome.seconds:.1f} s)\n    {outcome.detail}")
            elif outcome.caught:
                print(f"ok ({outcome.seconds:.1f} s)\n    {outcome.detail}")
            else:
                missed += 1
                print(f"UNDETECTED ({outcome.seconds:.1f} s)\n    {outcome.detail}")
    print_slowest(timings)
    if failures:
        return 2
    if missed or stale:
        # Both sentences, both counts, every run. Reporting only the undetected
        # one is how a stale anchor survives: the run fails, the reader fixes a
        # guard that was never broken, and the anchor is still stale next time.
        if missed:
            print(f"\nfault injection: FAIL, {missed} fault(s) went undetected")
        if stale:
            print(
                f"fault injection: FAIL, {stale} fault(s) no longer apply, so "
                f"they tested nothing. Each one is a string in this file that no "
                f"longer matches the code; re-anchor it before reading anything "
                f"into the undetected count."
            )
        return 1
    print("\nfault injection: pass, every fault was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
