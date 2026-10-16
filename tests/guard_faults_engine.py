# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Apply one fault, run the self-test in isolation, judge it, and restore."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from argus.config.schema import SHELL_VARS
from argus.exec_tempdir import make_exec_dir

DRIVER = Path("src/argus/driver/phase-driver.sh")

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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_fault(path: Path, fault: dict[str, Any]) -> None:
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


#: Where one fault's tmux socket base is made. The path has to be short: a tmux
#: server puts its socket under `TMUX_TMPDIR`, and a unix socket path is capped
#: (103 bytes usable on macOS, 107 on Linux). The fixture directory is not
#: capped, so the two get separate bases: a deep fixture scratch would otherwise
#: push the socket over the limit, the self-test would die before it printed a
#: verdict, and every driver-checked fault would be reported inconclusive. That
#: reads as an uncovered guard, and it is not one.
SHORT_TMP = "/tmp"


def _short_tmp_parent() -> str | None:
    """A short parent for one fault's tmux socket base, or ``None``."""
    candidate = Path(SHORT_TMP)
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return str(candidate)
    return None


def _isolated_env(scratch: str, tmux: str) -> dict[str, str]:
    """This process's environment, with one fault's own scratch space.

    The caller's Argus settings are dropped; `TMPDIR` points inside `scratch`
    and `TMUX_TMPDIR` at `tmux`, so this fault's fixtures and its tmux server
    cannot collide with another fault's or with a live line's. The socket base
    is separate because a unix socket path is capped and the fixture path is
    not.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(_SCRUBBED_PREFIXES) and name not in _SCRUBBED_NAMES
    }
    tmp = Path(scratch) / "tmp"
    tmp.mkdir()
    env["TMPDIR"] = str(tmp)
    env["TMUX_TMPDIR"] = tmux
    return env


def _kill_isolated_tmux(env: dict[str, str]) -> None:
    """Stop the fault's own tmux server, so a leaked session dies with it."""
    binary = shutil.which("tmux")
    if binary is None:
        return
    subprocess.run(
        [binary, "kill-server"], env=env, capture_output=True, text=True, timeout=30
    )


def run_selftest(
    repo: Path, driver: Path, check: str = "driver", cases: str | None = None
) -> tuple[list[str], int]:
    """Run the self-test in a one-fault scratch space and return its output.

    Returns ``(lines, exit status)``. The self-test never sees this machine's
    Argus settings, and its fixtures and tmux sessions are its own, so the same
    fault produces the same result whatever ran before it. ``cases`` names the
    driver self-test cases to run; the driver check reads it, the others do not.
    """
    # The tools live in the package above the driver's own folder, in the role
    # packages: `driver.parent` is the shell folder, not the package.
    package = driver.parent.parent
    command = (
        [sys.executable, str(repo / "tests/test_entrypoint.py")]
        if check == "entrypoint"
        else [sys.executable, str(package / "select/group.py"), "--self-test"]
        if check == "phase_group"
        else [
            sys.executable,
            str(package / "board/reservations.py"),
            "--self-test",
        ]
        if check == "phase_reservations"
        else [sys.executable, str(package / "select/next_phase.py"), "--self-test"]
        if check == "next_phase"
        else [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from argus.issues.selftest import main; sys.exit(main())",
            str(repo / "src"),
        ]
        if check == "issues"
        else [sys.executable, str(repo / "tests/issue_guards.py"), str(cases)]
        if check == "issue_guards"
        else ["bash", str(driver), "--self-test"]
    )
    # The self-test under test writes its own stubs and hooks into TMPDIR, which
    # `_isolated_env` points inside this scratch, so the scratch must be storage
    # the host executes. When none permits it, say so rather than hand the
    # self-test a directory that refuses and misreport every fault.
    scratch = make_exec_dir()
    if not scratch:
        return (
            ["fault harness: no temporary directory on this host runs files"],
            2,
        )
    try:
        with tempfile.TemporaryDirectory(
            prefix="ag-tmux-", dir=_short_tmp_parent()
        ) as tmux:
            env = _isolated_env(scratch, tmux)
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
    finally:
        # The fault's scratch space belongs to this harness and outlives the
        # self-test it was handed to, so here is the only place it can be
        # removed. It used to be left behind for the driver's sweep to find.
        shutil.rmtree(scratch, ignore_errors=True)


def judge(lines: list[str], rc: int, fault: dict[str, Any]) -> tuple[bool, str]:
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


def check_fault(
    repo: Path, driver: Path, fault: dict[str, Any]
) -> tuple[bool | None, str]:
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
        target.chmod(mode)
        if _digest(target) != digest:
            raise RestoreError(
                f"{target.name} was not restored byte-for-byte after the fault "
                "ran; restore it from git before ticking anything"
            )
