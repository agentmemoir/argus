# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run every Argus suite at once on this machine, and print what each one took.

`make test-all` starts the suites together. Each is its own process with its own
log, and each builds its own fixtures under a temporary root, so the suites share
no state. The output has one line per suite when it finishes, then one total.
A failing suite prints the end of its log.

CI runs the same suites as separate steps, so this list and the workflow describe
the same checks. `--only NAME` runs a subset, for a quick check of one suite, and
`--list` prints the suites without running them.
"""

import argparse
import concurrent.futures
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
#: Hang guard for one suite. A suite that runs past it fails with exit status 124.
SUITE_TIMEOUT_S = 3600


@dataclass(frozen=True)
class Suite:
    """One suite: a name, and the command that runs it from the repository root."""

    name: str
    argv: tuple[str, ...]


SUITES: tuple[Suite, ...] = (
    Suite(
        "pytest",
        (PY, "-m", "pytest", "-q", "-n", "auto", "--dist", "loadfile", "tests/"),
    ),
    Suite("driver-cases", (PY, "scripts/driver_cases.py")),
    Suite("entrypoint", (PY, "tests/test_entrypoint.py")),
    Suite("group", (PY, "-m", "argus.select.group", "--self-test")),
    Suite("gitsync", (PY, "-m", "argus.gitsync.main", "--self-test")),
    Suite("issues", (PY, "-m", "argus.issues.selftest")),
    Suite("reservations", (PY, "-m", "argus.board.reservations", "self-test")),
    Suite("guard-faults", (PY, "tests/guard_faults.py")),
)


@dataclass(frozen=True)
class Result:
    """The outcome of one suite: its exit status, its wall time, and its log."""

    name: str
    code: int
    seconds: float
    log: Path


def _text(value: bytes | str | None) -> str:
    """Process output as text, whether a timeout gave bytes, text or nothing."""
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def run_suite(suite: Suite, log_dir: Path) -> Result:
    """Run one suite to its end, writing its output to its own log."""
    log = log_dir / f"{suite.name}.log"
    started = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            suite.argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SUITE_TIMEOUT_S,
            check=False,
        )
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired as exc:
        output = _text(exc.stdout) + _text(exc.stderr)
        code = 124
    log.write_text(output, encoding="utf-8")
    return Result(suite.name, code, time.monotonic() - started, log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every Argus suite at once, and print each one's wall time."
    )
    parser.add_argument("--list", action="store_true", help="print the suites and exit")
    parser.add_argument(
        "--only", action="append", default=None, help="run only this suite; repeatable"
    )
    args = parser.parse_args(argv)

    suites = list(SUITES)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {suite.name for suite in SUITES}
        if unknown:
            print(
                f"error: no such suite: {', '.join(sorted(unknown))}", file=sys.stderr
            )
            return 2
        suites = [suite for suite in SUITES if suite.name in wanted]
    if args.list:
        for suite in suites:
            print(f"{suite.name}: {' '.join(suite.argv[1:])}")
        return 0

    log_dir = Path(tempfile.mkdtemp(prefix="argus-test-all-"))
    print(f"test-all: {len(suites)} suite(s) at once; logs in {log_dir}")
    started = time.monotonic()
    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(suites)) as pool:
        futures = [pool.submit(run_suite, suite, log_dir) for suite in suites]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            state = "pass" if result.code == 0 else f"FAIL (exit {result.code})"
            print(f"test-all suite {result.name}: {state}, {result.seconds:.1f} s")
    failed = [result for result in results if result.code != 0]
    for result in failed:
        print(f"--- {result.name}: last lines of {result.log}")
        print("\n".join(result.log.read_text(encoding="utf-8").splitlines()[-30:]))
    wall = time.monotonic() - started
    print(
        f"test-all total: {len(suites)} suite(s), {len(failed)} failed, "
        f"wall {wall:.1f} s"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
