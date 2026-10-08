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

So this harness does the only thing that settles it. Each fault below disables
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

The harness patches the source files beside it and restores them in a
``finally`` block. It checks each restored digest. Run it from a disposable
copy of the Argus folder to keep faults away from a live driver. The self-test
creates its own project, board, and run state; project history is not needed.

The self-test is the right thing to run here because it is hermetic: it builds
its own fixture repositories and its own board, and it writes only under its own
temporary directory. It is not the thing that pushed a claim onto the real board
during the incident this harness exists for — that was a hand-run of the driver,
which is precisely what this does not do.

    python3 scripts/guard_faults.py            # run every fault
    python3 scripts/guard_faults.py --list     # names and intents
    python3 scripts/guard_faults.py --only NAME
    python3 scripts/guard_faults.py --order reverse

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

# Source commands import the package from their own folder.
if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus.config import SHELL_VARS

DRIVER = Path("phase-driver.sh")

#: One hard stop for a single fault's check. The checks carry their own smaller
#: timeouts; this catches a hang. It is a cap, not a budget: the slowest
#: measured fault runs far below it.
SELFTEST_TIMEOUT = 600

#: Every fault: what it breaks, the exact text the patch must find, the
#: replacement, and the self-test line that proves it was caught.
#:
#: ``find`` and ``replace`` are literal strings, not regular expressions, so a
#: fault can never match more than the code it was written against.
FAULTS = (
    {
        "name": "entrypoint-halt-changed",
        "breaks": "the installed command changes the shell driver's halt and notification",
        "file": "bin/argus",
        "find": "args = sys.argv[1:]",
        "replace": "args = sys.argv[1:]\nos.environ['DRIVER_STUB_RC'] = '0'\nos.environ['DRIVER_STUB_SLEEP'] = '0'",
        "check": "entrypoint",
        "verdict": "entrypoint self-test:",
        "expect": "entrypoint session changed",
    },
    {
        "name": "launchability-guard-removed",
        "breaks": "the folder cannot be read and cannot be launched from",
        "file": "lib-driver-config.sh",
        "find": 'roadmap_preflight() {\n  PREFLIGHT_REASON=""',
        "replace": 'roadmap_preflight() {\n  PREFLIGHT_REASON=""\n'
                   '  return 0  # FAULT: guard disabled',
        "cases": "recovery_sources",
        "expect": "FAIL: a folder outside the checkout was allowed to launch",
    },
    {
        "name": "launchability-guard-below-reservation",
        "breaks": "the guard runs, but only after the phase is already claimed",
        "file": "lib-driver-drive.sh",
        "find": "  if ! roadmap_preflight; then\n"
                '    log "$PREFLIGHT_REASON; refusing to launch"\n'
                "    return 1\n  fi\n",
        "replace": "",
        # A second patch: the guard is cut from where it belongs and pasted
        # further down, which is the shape the real defect had. One fault with
        # two edits rather than two faults, because a guard that is missing and
        # a guard that is late are the same bug seen twice.
        "second_find": "  # The public checkout is put on the phase's own branch before the launch, from",
        "second_replace": (
            "  if ! roadmap_preflight; then\n"
            '    log "$PREFLIGHT_REASON; refusing to launch"\n'
            "    return 1\n  fi\n"
            "  # The public checkout is put on the phase's own branch before the launch, from"),
        "cases": "stranding",
        # The distinguishing line. With the guard late, the tick claims first and
        # refuses second, so the board check is what fires — and that check
        # exists for exactly this ordering, which is why it is the one named here
        # rather than the launch check.
        "expect": "so the line would be stranded",
    },
    {
        "name": "config-paths-anchored-on-cwd",
        "breaks": "relative config paths name different folders when Argus runs elsewhere",
        "file": "config.py",
        "find": "        candidate = config_dir / candidate",
        "replace": "        candidate = Path.cwd() / candidate  # FAULT: anchor on cwd",
        "check": "entrypoint",
        "verdict": "entrypoint self-test:",
        "expect": "config paths resolve against the config file",
    },
    {
        "name": "fixture-config-not-passed-to-tools",
        "breaks": "the phase notifier does not receive the fixture config",
        "file": "lib-driver-notify.sh",
        "find": '  local args=("${ARGUS_CONFIG_ARGS[@]}" --phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")',
        "replace": '  local args=(--phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")  # FAULT: config not passed',
        "cases": "launch",
        # The self-test checks the notifier's real start and completion messages.
        "expect": "expected one start notification",
    },
    {
        "name": "publisher-lookup-answers-nothing-found",
        "breaks": "the selector answers without its bundled publisher",
        "file": "roadmap_source.py",
        "find": "        from argus.gitsync import awaiting_review",
        "replace": "        return None  # FAULT: publisher unavailable but lookup succeeds\n        from argus.gitsync import awaiting_review",
        "cases": "recovery_sources",
        "expect": "--check failed on an unconfigured machine",
    },
    {
        "name": "group-declaration-missing-swallowed",
        "breaks": "a phase with no together declaration is handed a group of "
                  "itself instead of being refused",
        "file": "roadmap_source.py",
        "find": "        raise ReservationGroupMissing(\n"
                "            f\"{path} has no '### Reservation Group' section\", document=path)",
        "replace": "        return [f\"{number:06d}\"]  # FAULT: a missing "
                   "declaration is invented",
        "check": "next_phase",
        "verdict": "next_phase self-test:",
        "expect": "a phase with no together declaration is refused by name",
    },
    {
        "name": "group-claim-not-atomic",
        "breaks": "only the first member of a group is reserved, so half a "
                  "group can be claimed",
        "file": "phase_reservations.py",
        "find": "            records = []\n"
                "            for key in keys:\n"
                "                number = int(key)",
        "replace": "            records = []\n"
                   "            for key in keys[:1]:  # FAULT: half a group "
                   "is reserved\n"
                   "                number = int(key)",
        "check": "phase_group",
        "verdict": "phase_group self-test:",
        "expect": "a group claim writes every member or none",
    },
    {
        "name": "group-already-completed-swallowed",
        "breaks": "a group whose every member is completed is claimed instead "
                  "of being refused, so a finished group is reserved anew",
        "file": "phase_group.py",
        "find": "    if not remainder:",
        "replace": "    if False:  # FAULT: an all-completed group is not "
                   "refused",
        "check": "phase_group",
        "verdict": "phase_group self-test:",
        "expect": "an all-completed group is refused as already completed",
    },
    {
        "name": "cooldown-refusal-swallowed",
        "breaks": "an expired claim in its cooldown is claimed instead of "
                  "being refused, so two agents can run the same group",
        "file": "phase_reservations.py",
        "find": "        if existing.get(\"status\") == EXPIRED_STATUS:\n"
                "            remaining = _cooldown_remaining(existing, number, now)\n"
                "            if remaining > 0:\n"
                "                raise ReservationCooldown(number, remaining, existing)",
        "replace": "        if existing.get(\"status\") == EXPIRED_STATUS:\n"
                   "            remaining = 0  # FAULT: the cooldown is not refused",
        "check": "phase_reservations",
        "verdict": "coordination self-test:",
        "expect": "an expired group is refused during the cooldown",
    },
    {
        "name": "release-writes-halt-marker",
        "breaks": "a release stops the run through the halt path, so it writes "
                  "the halt marker instead of returning the group to the queue",
        "file": "lib-driver-run.sh",
        "find": "  if [ -f \"$RELEASE\" ]; then\n    rm -f \"$RELEASE\"\n"
                "  elif [ -n \"$CUR_PHASE\" ]; then",
        "replace": "  if false; then\n    rm -f \"$RELEASE\"\n"
                   "  elif [ -n \"$CUR_PHASE\" ]; then",
        "check": "driver",
        "cases": "release",
        "verdict": "driver self-test:",
        "expect": "release wrote the halt marker",
    },
    {
        "name": "group-branch-ignored",
        "breaks": "a group's one branch is not asked for, so each member gets a "
                  "branch of its own and the group publishes several",
        "file": "gitsync.py",
        "find": "        branch=group_branch(root, group, roadmap_dir) if group "
                "else None)",
        "replace": "        branch=None)  # FAULT: the group's one branch is "
                   "not used",
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the group's one branch covers every member",
    },
    {
        "name": "group-array-ignored",
        "breaks": "the group the board reports is not taken from the selection, so "
                  "a member of a held group is run as a single phase on a branch "
                  "of its own",
        "file": "lib-driver-select-flow.sh",
        "find": "        group_take_selection \"$selection\"\n"
                "        if [ \"$RECOVER_OWN_CLAIM\" = 1 ] \\\n",
        "replace": "        true  # FAULT: the group the board reports is not "
                   "taken\n"
                   "        if [ \"$RECOVER_OWN_CLAIM\" = 1 ] \\\n",
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the tick recorded the group's whole array",
    },
    {
        "name": "group-renewal-removed",
        "breaks": "the tick loop never renews a running claim, so the board "
                  "expires a live run's claim and another machine may take the "
                  "work",
        "file": "lib-driver-drive.sh",
        "find": "  claim_renew\n",
        "replace": "  true  # FAULT: the claim is never renewed\n",
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the tick renewed the group's claim while its run was alive",
    },
    {
        "name": "selector-refusal-swallowed",
        "breaks": "a refusal with no JSON result reports a Python traceback instead of nothing",
        "file": "lib-driver-select.sh",
        "find": 'try:\n    print(json.load(sys.stdin).get(sys.argv[1], ""))\n'
                "except ValueError:\n    pass",
        "replace": 'print(json.load(sys.stdin).get(sys.argv[1], ""))',
        "cases": "launch",
        # No FAIL line is expected, and that is the honest position: nothing
        # asserts that the driver's stderr stays free of tracebacks, because the
        # traceback is cosmetic and the log line that matters is unaffected. This
        # fault is listed so it is not mistaken for a covered guard, and it is
        # checked only for the property that is asserted: the driver still works.
        "expect": None,
        "expect_absent": "driver self-test: pass",
    },
)


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
            f"{text.count(find)} times, expected exactly 1")
    text = text.replace(find, fault["replace"], 1)
    if "second_find" in fault:
        second = fault["second_find"]
        if text.count(second) != 1:
            raise FaultNotApplicable(
                f"the second patch no longer matches: {second!r} appears "
                f"{text.count(second)} times, expected exactly 1")
        text = text.replace(second, fault["second_replace"], 1)
    path.write_text(text)


#: Environment names a fault's self-test must not inherit from its caller. A
#: setting the machine exports would otherwise let the host, and not the fault,
#: decide the result; the fixture points the driver at its own value instead.
_SCRUBBED_PREFIXES = ("CLIO_", "PHASE_", "DRIVER_", "ARGUS_")
_SCRUBBED_NAMES = frozenset({
    *SHELL_VARS.values(), "TMUX",
    "DISABLE_NOTIFY", "DISABLE_DISCORD",
})


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
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(_SCRUBBED_PREFIXES)
           and name not in _SCRUBBED_NAMES}
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
    subprocess.run([binary, "kill-server"], env=env,
                   capture_output=True, text=True, timeout=30)


def run_selftest(driver, check="driver", cases=None):
    """Run the self-test in a one-fault scratch space and return its output.

    Returns ``(lines, exit status)``. The self-test never sees this machine's
    Argus settings, and its fixtures and tmux sessions are its own, so the same
    fault produces the same result whatever ran before it. ``cases`` names the
    driver self-test cases to run; the driver check reads it, the others do not.
    """
    command = ([sys.executable, str(driver.parent / "test_entrypoint.py")]
               if check == "entrypoint"
               else [sys.executable, str(driver.parent / "phase_group.py"),
                     "--self-test"]
               if check == "phase_group"
               else [sys.executable, str(driver.parent / "phase_reservations.py"),
                     "--self-test"]
               if check == "phase_reservations"
               else [sys.executable, str(driver.parent / "next_phase.py"),
                     "--self-test"]
               if check == "next_phase"
               else ["bash", str(driver), "--self-test"])
    with tempfile.TemporaryDirectory(prefix="ag-", dir=_scratch_parent()) as scratch:
        env = _isolated_env(scratch)
        if cases:
            env["SELFTEST_CASES"] = cases
        try:
            proc = subprocess.run(
                command, cwd=driver.parent.parent, env=env,
                capture_output=True, text=True, timeout=SELFTEST_TIMEOUT)
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
        return False, (f"inconclusive: the self-test did not reach its verdict "
                       f"({reason}); exit status {rc}. This fault is untested "
                       f"until the self-test runs to the end, not undetected.")
    if fault.get("expect"):
        hits = [ln for ln in lines
                if ln.startswith("FAIL:") and fault["expect"] in ln]
        if not hits:
            return False, (f"not detected: no FAIL line mentions "
                           f"{fault['expect']!r}; the verdict was {verdict!r}")
        # Detected and still healthy would mean the self-test reported both,
        # which is a self-test bug rather than a detection. Failing loudly beats
        # accepting a partial result.
        if not verdict.endswith("FAIL"):
            return False, (f"the fault produced {hits[0]!r} but the verdict was "
                           f"{verdict!r}")
        return True, f"caught: {hits[0]}"
    # No FAIL line is expected, so this fault's own claim is that the driver still
    # works. Asserting that is what stops the entry from becoming a fault nobody
    # maintains.
    if fault.get("expect_absent") and not any(
            ln.strip() == fault["expect_absent"] for ln in lines):
        return False, (f"expected the driver to keep working "
                       f"({fault['expect_absent']!r}) and it did not; the verdict "
                       f"was {verdict!r}")
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
        lines, rc = run_selftest(driver, fault.get("check", "driver"),
                                 fault.get("cases"))
        return judge(lines, rc, fault)
    finally:
        target.write_bytes(original)
        os.chmod(target, mode)
        if _digest(target) != digest:
            raise RestoreError(
                f"{target.name} was not restored byte-for-byte after the fault "
                "ran; restore it from git before ticking anything")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Break each launch guard and prove a self-test notices.")
    parser.add_argument("--list", action="store_true",
                        help="print every fault, what it breaks, and what catches it")
    parser.add_argument("--only", action="append", default=None,
                        help="run only this fault; repeatable")
    parser.add_argument("--order", choices=("declared", "reverse", "shuffle"),
                        default="declared",
                        help="fault order; reverse and shuffle show whether a "
                             "result depends on which fault ran first")
    parser.add_argument("--seed", type=int, default=0,
                        help="seed for --order shuffle")
    args = parser.parse_args(argv)

    if args.list:
        for fault in FAULTS:
            caught_by = fault.get("expect") or fault.get("expect_absent")
            print(f"{fault['name']}\n"
                  f"    breaks:  {fault['breaks']}\n"
                  f"    file:    {fault.get('file', DRIVER)}\n"
                  f"    caught:  {caught_by}")
        return 0

    selected = list(FAULTS)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {fault["name"] for fault in FAULTS}
        if unknown:
            print(f"error: no such fault: {', '.join(sorted(unknown))}",
                  file=sys.stderr)
            return 2
        selected = [f for f in FAULTS if f["name"] in wanted]

    if args.order == "reverse":
        selected = list(reversed(selected))
    elif args.order == "shuffle":
        random.Random(args.seed).shuffle(selected)

    repo = Path(__file__).resolve().parent
    driver = repo / DRIVER
    if not driver.is_file():
        print(f"error: no driver at {driver}", file=sys.stderr)
        return 2

    print(f"fault injection: {len(selected)} fault(s), order={args.order}, "
          f"each on the real code, restored after every run")
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
            print(f"fault injection: FAIL, {stale} fault(s) no longer apply, so "
                  f"they tested nothing. Each one is a string in this file that no "
                  f"longer matches the code; re-anchor it before reading anything "
                  f"into the undetected count.")
        return 1
    print("\nfault injection: pass, every fault was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
