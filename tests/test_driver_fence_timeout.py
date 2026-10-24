# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The board fence is bounded even on a host with no ``timeout``.

``board_assert_owner_cmd`` used ``timeout``/``gtimeout`` when one existed and ran
the board call unbounded when neither did — while the driver lock (fd 9) was
held. A board that never answers then held the lock for as long as the process
lived. The portable bound runs the call in the background, polls its pid, and
kills it; a timeout is a refusal, exactly as a timer's kill is.

``PATH`` here holds one entry: a directory with ``sleep`` and nothing else. That
is what makes "no ``timeout`` and no ``gtimeout``" true regardless of the host.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.exec_tempdir import exec_tempdir

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
# Resolved before the test narrows PATH to a directory holding only `sleep`.
BASH = shutil.which("bash")

# The stand-in for `python board/reservations.py assert-owner ...`. It records its
# own pid, answers on both streams, and sleeps: one process, so a SIGTERM reaches
# it directly and the test can prove nothing is left behind.
STUB = """#!{python}
import os, sys, time
from pathlib import Path
state = Path(os.environ["FENCE_STUB_STATE"])
(state / "pid").write_text(str(os.getpid()))
print("board-said-out")
print("board-said-err", file=sys.stderr)
time.sleep(float(os.environ["FENCE_STUB_SLEEP"]))
(state / "finished").write_text("done")
sys.exit(int(os.environ["FENCE_STUB_EXIT"]))
"""

HARNESS = """
set -euo pipefail
. "$TEST_DRIVER/lib-driver-fence.sh"
PYTHON="$FENCE_STUB"
ARGUS="$TEST_DRIVER/.."
REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT"
PHASE_MACHINE_ID=fixture
ARGUS_CONFIG_ARGS=(--config fixture.json)
BOARD_KIND_ARGS=()
rc=0
board_assert_owner_cmd 100020 rsv-fixture 7 || rc=$?
printf 'rc=%s\\n' "$rc"
"""


def _probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Path:
    """A PATH with sleep and nothing else, a stub board command, and a run dir."""
    stub = exec_tempdir(purpose="the fence timeout test's stub board")
    stub_dir = Path(stub.name)
    binary = stub_dir / "board.py"
    binary.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)
    toolbin = tmp_path / "toolbin"
    toolbin.mkdir()
    sleep = shutil.which("sleep")
    assert sleep is not None, "the host has no sleep, so the poll cannot be tested"
    (toolbin / "sleep").symlink_to(sleep)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PATH", str(toolbin))
    monkeypatch.setenv("FENCE_STUB", str(binary))
    monkeypatch.setenv("FENCE_STUB_STATE", str(state))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return state


def _run(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    assert BASH is not None, "the host has no bash"
    started = time.monotonic()
    done = subprocess.run(
        [BASH, "-c", HARNESS],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "FENCE_STUB": os.environ["FENCE_STUB"],
            "FENCE_STUB_STATE": os.environ["FENCE_STUB_STATE"],
            "FENCE_STUB_SLEEP": os.environ["FENCE_STUB_SLEEP"],
            "FENCE_STUB_EXIT": os.environ["FENCE_STUB_EXIT"],
            "ARGUS_FENCE_TIMEOUT_S": os.environ["ARGUS_FENCE_TIMEOUT_S"],
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    return done, time.monotonic() - started


def test_a_hung_board_is_killed_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bound is 5 s and the stub sleeps 60 s. A 1 s bound could fire before a
    # loaded machine starts the stub, which then never records its pid. The stub
    # outlives the bound by far, and the run's own 30 s timeout stays the hang guard.
    state = _probe(
        tmp_path,
        monkeypatch,
        FENCE_STUB_SLEEP="60",
        FENCE_STUB_EXIT="0",
        ARGUS_FENCE_TIMEOUT_S="5",
    )

    done, elapsed = _run(tmp_path)

    assert done.returncode == 0, done.stderr
    assert "rc=124" in done.stdout, done.stdout
    assert elapsed < 15, f"the bound did not fire in time: {elapsed:.1f}s"
    pid = int((state / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (state / "finished").exists(), "the killed board call ran to the end"
    assert (
        "did not answer within 5s"
        in (tmp_path / "assert-owner-100020.json").read_text()
    )


def test_a_board_that_answers_keeps_its_own_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(
        tmp_path,
        monkeypatch,
        FENCE_STUB_SLEEP="0",
        FENCE_STUB_EXIT="7",
        ARGUS_FENCE_TIMEOUT_S="20",
    )

    done, _elapsed = _run(tmp_path)

    assert done.returncode == 0, done.stderr
    # The child's status, not a made-up one: a refusal still reaches the caller
    # as the code the board gave.
    assert "rc=7" in done.stdout, done.stdout
    out = (tmp_path / "assert-owner-100020.json").read_text()
    assert "board-said-out" in out and "board-said-err" in out
