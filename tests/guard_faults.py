#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
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

The harness patches the driver's source files in place and restores them in a
``finally`` block. It checks each restored digest. Run it from a disposable
copy of the Argus repository, so a fault cannot touch a live driver. The
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
for it, not just observed once.

Each fault runs in its own temporary directory and its own tmux server. The
child process drops all config settings from its environment, so the fixture
must pass its config file to every tool.

Exit status is 1 when any fault went undetected, 2 when the driver could not be
restored. With --list, 0 always.
"""

import argparse
import random
import sys
from pathlib import Path

from faults import FAULTS
from guard_faults_engine import DRIVER, RestoreError, check_fault


def main(argv=None):
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

    print(
        f"fault injection: {len(selected)} fault(s), order={args.order}, "
        f"each on the real code, restored after every run"
    )
    missed = 0
    stale = 0
    for fault in selected:
        sys.stdout.write(f"  {fault['name']} ... ")
        sys.stdout.flush()
        try:
            caught, detail = check_fault(repo, driver, fault)
        except RestoreError as exc:
            print("NOT RESTORED")
            print(f"    {exc}", file=sys.stderr)
            return 2
        if caught is None:
            # Its own line, its own count, and its own sentence in the summary. A
            # stale anchor reads as an uncovered guard otherwise, and the reader
            # goes looking for a defect in the driver that is not there.
            stale += 1
            print(f"NOT APPLICABLE\n    {detail}")
        elif caught:
            print(f"ok\n    {detail}")
        else:
            missed += 1
            print(f"UNDETECTED\n    {detail}")
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
