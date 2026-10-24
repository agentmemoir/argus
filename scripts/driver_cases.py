# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run the driver self-test cases as concurrent jobs, one driver process per job.

The driver self-test runs its cases one after another in one process. Each case
builds its own fixture: a temporary root with its own git repositories, board and
tmux socket. SELFTEST_CASES runs a named subset in declared order, so one process
per case gets the isolation the single run gives it, and the processes can run side
by side. The leftover sweep only touches fixtures whose owning process is dead, so
a live job's fixture is never swept.

ORDERED_GROUPS names cases that run in one process, in declared order. It is empty:
each case builds the state it needs, so each case runs as its own job. A case that
cannot build its own state belongs in a group, and the shard planner keeps a group
whole.

Each job has a timeout and a log. The pool has one worker per CPU.
"""

import concurrent.futures
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "src" / "argus" / "driver" / "lib-selftest.sh"
DRIVER = ROOT / "bin" / "argus"
#: Hang guard for one job. A job that runs past it fails with a reason.
JOB_TIMEOUT_S = 900
#: Cases that run in one process, in declared order. See the docstring.
ORDERED_GROUPS: list[tuple[str, ...]] = []
PASS_LINE = "driver self-test: pass"


@dataclass(frozen=True)
class Result:
    """The outcome of one job: a driver process that ran one or more cases."""

    name: str
    ok: bool
    seconds: float
    reason: str
    log: Path


def declared_cases() -> list[str]:
    """The case names, in the order the driver self-test runs them."""
    text = CASES_FILE.read_text(encoding="utf-8")
    return re.findall(r"^\s*selftest_run_case (\w+)\s*$", text, flags=re.MULTILINE)


def jobs(cases: list[str]) -> list[tuple[str, ...]]:
    """One job per case in no group, and one job per ordered group."""
    group_of = {case: group for group in ORDERED_GROUPS for case in group}
    units: list[tuple[str, ...]] = []
    for case in cases:
        group = group_of.get(case)
        if group is None:
            units.append((case,))
        elif group not in units:
            units.append(group)
    return units


def _text(value: bytes | str | None) -> str:
    """Process output as text, whether the timeout gave bytes, text or nothing."""
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def run_job(names: tuple[str, ...], log_dir: Path) -> Result:
    """Run one driver process that runs `names` in declared order."""
    label = "+".join(names)
    log = log_dir / f"{label}.log"
    env = dict(os.environ, SELFTEST_CASES=" ".join(names))
    started = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            [str(DRIVER), "--self-test"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=JOB_TIMEOUT_S,
            check=False,
        )
        output = done.stdout + done.stderr
        ok = done.returncode == 0 and PASS_LINE in done.stdout.splitlines()
        reason = "" if ok else f"exit {done.returncode}, no pass line"
    except subprocess.TimeoutExpired as exc:
        output = _text(exc.stdout) + _text(exc.stderr)
        ok, reason = False, f"no result within {JOB_TIMEOUT_S} s"
    log.write_text(output, encoding="utf-8")
    return Result(label, ok, time.monotonic() - started, reason, log)


def main() -> int:
    started = time.monotonic()
    workers = os.cpu_count() or 1
    log_dir = Path(tempfile.mkdtemp(prefix="argus-driver-jobs-"))
    units = jobs(declared_cases())
    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_job, unit, log_dir) for unit in units]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            state = "pass" if result.ok else f"FAIL: {result.reason}"
            print(f"driver job {result.name}: {state} ({result.seconds:.1f} s)")
    print("driver jobs: slowest 10")
    for result in sorted(results, key=lambda r: r.seconds, reverse=True)[:10]:
        print(f"driver jobs:   {result.seconds:7.1f} s  {result.name}")
    failed = [result for result in results if not result.ok]
    for result in failed:
        print(f"--- {result.name}: last lines of {result.log}")
        print("\n".join(result.log.read_text(encoding="utf-8").splitlines()[-40:]))
    wall = time.monotonic() - started
    print(
        f"driver jobs: {len(units)} jobs on {workers} workers, "
        f"{len(failed)} failed, wall {wall:.1f} s"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
