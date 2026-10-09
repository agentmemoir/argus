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
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from argus.config import SHELL_VARS
from faults import FAULTS

DRIVER = Path("src/argus/phase-driver.sh")

#: One hard stop for a single fault's check. The checks carry their own smaller
#: timeouts; this catches a hang. It is a cap, not a budget: the slowest
#: measured fault runs far below it.
SELFTEST_TIMEOUT = 600


class FaultError(RuntimeError):
    """A fault could not be applied, so it proved nothing."""


class FaultNotApplicable(FaultError):
    """The code a fault targets has changed, so the fault never ran.

    Kept apart from FaultError because the two are different findings and
    counting them as one hides both. A guard that stopped being covered is a
    statement about the driver. A fault whose anchor has drifted is a statement
    about the harness, and it is the one that goes unnoticed: the run reports a
    fault undetected, a reader concludes a launch guard is uncovered, and the
    real problem is that a string in this file no longer appears in the driver.
    Both fail the run. Only one of them is about the driver.
    """


class RestoreError(RuntimeError):
    """The driver was not put back, which is worse than any fault below."""


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_fault(path, fault):
    """Patch one fault into the driver. Raises when a patch no longer matches."""
    text = path.read_text()
    find = fault["find"]
    if text.count(find) != 1:
        raise FaultNotApplicable(
            f"the code this fault targets has changed: {find!r} appears "
            f"{text.count(find)} times, expected exactly 1"
        )
    text = text.replace(find, fault["replace"], 1)
    if "second_find" in fault:
        second = fault["second_find"]
        if text.count(second) != 1:
            raise FaultNotApplicable(
                f"the second patch no longer matches: {second!r} appears "
                f"{text.count(second)} times, expected exactly 1"
            )
        text = text.replace(second, fault["second_replace"], 1)
    path.write_text(text)


#: Environment names a fault's self-test must not inherit from its caller. A
#: setting the machine exports would otherwise let the host, and not the fault,
#: decide the result; the fixture points the driver at its own value instead.
_SCRUBBED_PREFIXES = ("CLIO_", "PHASE_", "DRIVER_", "ARGUS_")
_SCRUBBED_NAMES = frozenset(
    {
        *SHELL_VARS.values(),
        "TMUX",
        "DISABLE_NOTIFY",
        "DISABLE_DISCORD",
    }
)


#: Where one fault's scratch space is made. The path has to be short: a tmux
#: server puts its socket under `TMUX_TMPDIR`, and a unix socket path is capped
#: (104 bytes on macOS, 108 on Linux). A caller's own `TMPDIR` can be deep enough
#: that the socket path overflows, and then the driver self-test dies before it
#: prints a verdict, so every driver-checked fault is reported inconclusive. That
#: reads as an uncovered guard, and it is not one.
SHORT_TEMP = "/tmp"


def _scratch_parent():
    """A short parent for one fault's scratch space, or ``None`` for the default."""
    candidate = Path(SHORT_TEMP)
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return str(candidate)
    return None


def _isolated_env(scratch):
    """This process's environment, with one fault's own scratch space.

    The caller's Argus settings are dropped and `TMPDIR` and `TMUX_TMPDIR` point
    inside `scratch`, so this fault's fixtures and its tmux server cannot collide
    with another fault's or with a live line's.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(_SCRUBBED_PREFIXES) and name not in _SCRUBBED_NAMES
    }
    tmp, tmux = Path(scratch) / "tmp", Path(scratch) / "tmux"
    tmp.mkdir()
    tmux.mkdir()
    env["TMPDIR"] = str(tmp)
    env["TMUX_TMPDIR"] = str(tmux)
    return env


def _kill_isolated_tmux(env):
    """Stop the fault's own tmux server, so a leaked session dies with it."""
    binary = shutil.which("tmux")
    if binary is None:
        return
    subprocess.run(
        [binary, "kill-server"], env=env, capture_output=True, text=True, timeout=30
    )


def run_selftest(repo, driver, check="driver", cases=None):
    """Run the self-test in a one-fault scratch space and return its output.

    Returns ``(lines, exit status)``. The self-test never sees this machine's
    Argus settings, and its fixtures and tmux sessions are its own, so the same
    fault produces the same result whatever ran before it. ``cases`` names the
    driver self-test cases to run; the driver check reads it, the others do not.
    """
    command = (
        [sys.executable, str(repo / "tests/test_entrypoint.py")]
        if check == "entrypoint"
        else [sys.executable, str(driver.parent / "phase_group.py"), "--self-test"]
        if check == "phase_group"
        else [
            sys.executable,
            str(driver.parent / "phase_reservations.py"),
            "--self-test",
        ]
        if check == "phase_reservations"
        else [sys.executable, str(driver.parent / "next_phase.py"), "--self-test"]
        if check == "next_phase"
        else ["bash", str(driver), "--self-test"]
    )
    with tempfile.TemporaryDirectory(prefix="ag-", dir=_scratch_parent()) as scratch:
        env = _isolated_env(scratch)
        if cases:
            env["SELFTEST_CASES"] = cases
        try:
            proc = subprocess.run(
                command,
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=SELFTEST_TIMEOUT,
            )
        finally:
            _kill_isolated_tmux(env)
    return proc.stdout.splitlines() + proc.stderr.splitlines(), proc.returncode


def judge(lines, rc, fault):
    """Decide whether the self-test caught this fault. Returns (caught, detail).

    A run that stopped before its own verdict line detected nothing, whatever the
    fault was, and saying so is the whole finding: a self-test that dies part way
    cannot tell a working guard from a missing one. Reading the absence of the
    verdict as "this fault went undetected" buried that inside a per-fault
    message, which is where it stayed invisible while every guard in the driver
    reported itself uncovered.
    """
    prefix = fault.get("verdict", "driver self-test:")
    verdict = next((ln for ln in lines if ln.startswith(prefix)), "")
    if not verdict:
        stopped = [ln for ln in lines if ln.startswith("FAIL: the self-test")]
        reason = stopped[-1] if stopped else "no verdict line was printed"
        return False, (
            f"inconclusive: the self-test did not reach its verdict "
            f"({reason}); exit status {rc}. This fault is untested "
            f"until the self-test runs to the end, not undetected."
        )
    if fault.get("expect"):
        hits = [ln for ln in lines if ln.startswith("FAIL:") and fault["expect"] in ln]
        if not hits:
            return False, (
                f"not detected: no FAIL line mentions "
                f"{fault['expect']!r}; the verdict was {verdict!r}"
            )
        # Detected and still healthy would mean the self-test reported both,
        # which is a self-test bug rather than a detection. Failing loudly beats
        # accepting a partial result.
        if not verdict.endswith("FAIL"):
            return False, (
                f"the fault produced {hits[0]!r} but the verdict was {verdict!r}"
            )
        return True, f"caught: {hits[0]}"
    # No FAIL line is expected, so this fault's own claim is that the driver still
    # works. Asserting that is what stops the entry from becoming a fault nobody
    # maintains.
    if fault.get("expect_absent") and not any(
        ln.strip() == fault["expect_absent"] for ln in lines
    ):
        return False, (
            f"expected the driver to keep working "
            f"({fault['expect_absent']!r}) and it did not; the verdict "
            f"was {verdict!r}"
        )
    return True, f"the driver still works under this fault ({verdict})"


def check_fault(repo, driver, fault):
    """Patch one fault in, run the self-test, restore, report.

    The restore happens in a ``finally``, so an exception or a signal still puts
    the file back. Its digest is checked afterwards rather than assumed.

    A fault names its own file. Most target the driver, but a guard that lives in
    one of the tools beside it is just as capable of being broken silently, and a
    harness that can only patch one file cannot prove those.
    """
    target = repo / fault.get("file", DRIVER)
    if not target.is_file():
        return False, f"fault not applied: no such file: {target}"
    original = target.read_bytes()
    digest = _digest(target)
    mode = target.stat().st_mode
    try:
        apply_fault(target, fault)
    except FaultNotApplicable as exc:
        return None, f"fault not applicable, so it tested nothing: {exc}"
    except FaultError as exc:
        return False, f"fault not applied: {exc}"
    try:
        lines, rc = run_selftest(
            repo, driver, fault.get("check", "driver"), fault.get("cases")
        )
        return judge(lines, rc, fault)
    finally:
        target.write_bytes(original)
        os.chmod(target, mode)
        if _digest(target) != digest:
            raise RestoreError(
                f"{target.name} was not restored byte-for-byte after the fault "
                "ran; restore it from git before ticking anything"
            )


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
