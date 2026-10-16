# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A hard-killed self-test run is recovered by the next one.

SIGKILL runs no trap, so the teardown in the driver's self-test cannot clean up
after a run that was killed that hard. The run records its own pid and its tmux
socket directory inside its fixture, and the next run stops the sessions that
name that pid, stops the server behind a socket that sits inside the fixture, and
removes the fixture. A fixture whose recorded run is still alive is left alone,
because two self-tests on one machine must not delete each other's.

``selftest_recover_leftovers`` takes the fixture bases as arguments, so each case
here gets a base of its own. The base has to be short: a unix socket path is
capped (103 bytes usable on macOS), and the socket of a fixture under ``tmp_path``
is too deep for one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ARGUS = Path(__file__).resolve().parents[1]
LIB = ARGUS / "src/argus/driver/lib-selftest.sh"

#: A pid no process can hold: a pid is 32-bit, and no host lets it get this
#: large, so a fixture that records it always reads as abandoned. A real pid that
#: has been reaped would pass until the kernel reused the number.
IMPOSSIBLE_PID = 999999999

#: The short base the tmux socket of a fixture is made under, for the reason in
#: the module docstring.
SHORT_TMP = "/tmp"

#: A fixture older than this is swept on age alone when it has no record.
STALE_SECONDS = 7200


def _env(socket: str) -> dict[str, str]:
    """This process's environment, pointed at one socket base.

    ``TMUX`` is dropped so a test run inside tmux cannot aim these commands at
    the developer's own server.
    """
    env = {name: value for name, value in os.environ.items() if name != "TMUX"}
    env["TMUX_TMPDIR"] = socket
    return env


def _tmux() -> str:
    binary = shutil.which("tmux")
    if binary is None:
        pytest.skip("tmux is needed to check that a leaked session is stopped")
    return binary


def _sessions(tmux: str, socket: str) -> list[str]:
    result = subprocess.run(
        [tmux, "list-sessions", "-F", "#{session_name}"],
        env=_env(socket),
        capture_output=True,
        text=True,
    )
    return result.stdout.split()


def _fixture(base: Path, name: str, pid: int) -> tuple[Path, str]:
    """One fixture directory with a record naming ``pid``, and its socket base."""
    fixture = base / f"argus-selftest-{name}"
    socket = fixture / "tmux"
    socket.mkdir(parents=True)
    (fixture / "owner").write_text(f"pid={pid}\nsocket={socket}\n", encoding="utf-8")
    return fixture, str(socket)


def _session(tmux: str, socket: str, name: str) -> None:
    subprocess.run(
        [tmux, "new-session", "-d", "-s", name, "sleep 300"],
        env=_env(socket),
        check=True,
        capture_output=True,
        text=True,
    )


def _recover(base: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; . "$1"; selftest_recover_leftovers "$2"',
            "_",
            str(LIB),
            str(base),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_next_run_recovers_a_hard_killed_run_and_spares_a_live_one() -> None:
    """The abandoned fixture and its session go; a live run's pair stays."""
    tmux = _tmux()
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=SHORT_TMP) as tmp:
        base = Path(tmp)
        abandoned, abandoned_socket = _fixture(base, "abandoned", IMPOSSIBLE_PID)
        live, live_socket = _fixture(base, "live", os.getpid())
        live_name = f"ar-driver-selftest-{os.getpid()}"
        _session(tmux, abandoned_socket, f"ar-driver-selftest-{IMPOSSIBLE_PID}")
        _session(tmux, live_socket, live_name)
        try:
            _recover(base)
            assert not abandoned.exists()
            assert _sessions(tmux, abandoned_socket) == []
            assert live.is_dir()
            assert _sessions(tmux, live_socket) == [live_name]
        finally:
            subprocess.run(
                [tmux, "kill-server"], env=_env(live_socket), capture_output=True
            )


def test_a_fixture_with_no_record_is_still_swept_by_age_alone() -> None:
    """A directory made before the record existed keeps the old behaviour.

    Both are removed on age and spared while fresh, which is what stops two
    self-tests on one machine from deleting each other's fixture.
    """
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=SHORT_TMP) as tmp:
        base = Path(tmp)
        stale = base / "argus-selftest-stale"
        fresh = base / "argus-selftest-fresh"
        stale.mkdir()
        fresh.mkdir()
        moment = time.time() - STALE_SECONDS
        os.utime(stale, (moment, moment))
        _recover(base)
        assert not stale.exists()
        assert fresh.is_dir()


def test_the_sweep_looks_where_the_fixture_is_actually_put() -> None:
    """A fixture under ``TMPDIR`` is found, which is where a macOS host puts it.

    The temporary-directory helper prefers the default temporary directory, so a
    sweep that named ``/tmp`` alone never looked where the fixture went on a host
    whose ``TMPDIR`` is somewhere else: the killed run's fixture and its sessions
    stayed until someone removed them by hand.
    """
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=SHORT_TMP) as tmp:
        base = Path(tmp)
        abandoned, _socket = _fixture(base, "abandoned", IMPOSSIBLE_PID)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'set -euo pipefail; . "$1"; ARGUS="$2"; PYTHON="$3"; selftest_tmpdir',
                "_",
                str(LIB),
                str(ARGUS / "src/argus"),
                sys.executable,
            ],
            env=dict(os.environ, TMPDIR=str(base)),
            capture_output=True,
            text=True,
        )
        created = result.stdout.strip()
        try:
            assert result.returncode == 0, result.stderr
            assert not abandoned.exists(), "the sweep did not look in TMPDIR"
            # The helper really does put the fixture here, which is why the
            # sweep has to look here.
            assert Path(created).parent.resolve() == base.resolve(), created
        finally:
            if created:
                shutil.rmtree(created, ignore_errors=True)
