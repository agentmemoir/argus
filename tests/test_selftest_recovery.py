# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The recovery sweep for a self-test run that could not clean up after itself.

SIGKILL runs no trap, so a run killed that hard leaves its fixture behind, with
the tmux server it started and the idle monitor inside it. The run records its own
pid and its socket directory in `<fixture>/owner`, and the next run stops the
sessions that name that pid, stops the server behind a socket inside the fixture,
and removes the fixture. A fixture whose recorded run is still alive is left
alone, because two self-tests on one machine must not delete each other's.

Two properties matter more than the happy path and each has a case here: a
leftover must never stop the run that finds it, and a fixture must never be
removed while a server that owns it may still be running.

The sweep asks its helper where a fixture can be, so every case replaces that
answer with a base of its own and the tests stay off every other run's
directories. One case checks the answer itself.
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

from argus.exec_tempdir import candidate_bases

ARGUS = Path(__file__).resolve().parents[1]
LIB = ARGUS / "src/argus/driver/lib-selftest-recover.sh"

#: A pid no process can hold: a pid is 32-bit, and no host lets it get this large,
#: so a fixture that records it always reads as abandoned. A real pid that has
#: been reaped would pass until the kernel reused the number.
IMPOSSIBLE_PID = 999999999

#: A fixture older than this is swept on age alone when it has no usable record.
STALE_SECONDS = 7200

#: The short base a tmux socket needs: a unix socket path is capped at about 100
#: bytes and the path under `tmp_path` is longer than that.
SHORT_TMP = "/tmp"


def _short_tmp() -> str:
    """The short base a tmux socket needs, or a skip naming why there is none."""
    candidate = Path(SHORT_TMP)
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return SHORT_TMP
    pytest.skip(f"{SHORT_TMP} cannot hold a unix socket on this host")


def _env(socket: str) -> dict[str, str]:
    """This process's environment, pointed at one socket base.

    ``TMUX`` is dropped so a test run inside tmux cannot aim these commands at the
    developer's own server.
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


def _recover(base: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    """Run the sweep with its bases narrowed to one base of this test's own."""
    env = dict(os.environ, ARGUS_SELFTEST_BASES=str(base), **extra)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; . "$1"; selftest_recover_leftovers',
            "_",
            str(LIB),
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_next_run_recovers_a_hard_killed_run_and_spares_a_live_one() -> None:
    """The abandoned fixture and its session go; a live run's pair stays."""
    tmux = _tmux()
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=_short_tmp()) as tmp:
        base = Path(tmp)
        abandoned, abandoned_socket = _fixture(base, "abandoned", IMPOSSIBLE_PID)
        live, live_socket = _fixture(base, "live", os.getpid())
        live_name = f"ar-driver-selftest-{os.getpid()}"
        _session(tmux, abandoned_socket, f"ar-driver-selftest-{IMPOSSIBLE_PID}")
        _session(tmux, live_socket, live_name)
        try:
            result = _recover(base)
            assert result.returncode == 0, result.stderr
            assert not abandoned.exists()
            assert _sessions(tmux, abandoned_socket) == []
            assert live.is_dir()
            assert _sessions(tmux, live_socket) == [live_name]
        finally:
            # Both servers, because a failed assertion must not leave either one
            # running: the abandoned one is the leak this file exists to prevent.
            for socket in (abandoned_socket, live_socket):
                subprocess.run(
                    [tmux, "kill-server"], env=_env(socket), capture_output=True
                )


def test_a_fixture_with_no_usable_record_is_swept_by_age_alone() -> None:
    """A directory with no record, or one that cannot be read, is decided by age.

    Reading an unreadable record under `set -e` is what turned one awkward
    leftover into a run that skipped every check, so the read is guarded and the
    age rule decides instead. Both fixtures here record a pid that is gone; only
    the older one may go.
    """
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=_short_tmp()) as tmp:
        base = Path(tmp)
        stale, _ = _fixture(base, "stale", IMPOSSIBLE_PID)
        fresh, _ = _fixture(base, "fresh", IMPOSSIBLE_PID)
        for fixture in (stale, fresh):
            (fixture / "owner").chmod(0o000)
        moment = time.time() - STALE_SECONDS
        os.utime(stale, (moment, moment))
        result = _recover(base)
        assert result.returncode == 0, result.stderr
        assert not stale.exists()
        assert fresh.is_dir()


def test_a_fixture_whose_sessions_cannot_be_stopped_is_left_in_place() -> None:
    """Nothing is removed while a server that owns it may still be running.

    Removing the fixture unlinks the socket, and a server whose socket is gone
    cannot be found or stopped again: that is the one way to turn a leak someone
    could find into one nobody can.
    """
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=_short_tmp()) as tmp:
        base = Path(tmp)
        fixture, _socket = _fixture(base, "unreachable", IMPOSSIBLE_PID)
        result = _recover(base, CMD_TMUX="/nonexistent-tmux-for-this-check")
        assert result.returncode == 0, result.stderr
        assert fixture.is_dir(), "removed without stopping the server behind it"
        assert "still running" in result.stderr


def test_the_sweep_bases_are_the_ones_the_helper_chooses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep looks where a fixture really goes, straight from the helper.

    The helper prefers the default temporary directory, so a sweep that kept its
    own copy of the list and named `/tmp` alone never found the fixture on a host
    whose `TMPDIR` is somewhere else.
    """
    with tempfile.TemporaryDirectory(prefix="ag-recover-", dir=_short_tmp()) as tmp:
        base = Path(tmp)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'set -euo pipefail; . "$1"; selftest_fixture_bases',
                "_",
                str(LIB),
            ],
            env=dict(
                os.environ,
                TMPDIR=str(base),
                PYTHON=sys.executable,
                ARGUS=str(ARGUS / "src/argus"),
            ),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        monkeypatch.setattr(tempfile, "tempdir", str(base))
        assert result.stdout.splitlines() == candidate_bases()
