# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tmux server must not keep the checkout lock after its phase ends.

The phase takes the checkout lock inside its own window process. The first test runs
that phase in a window of a tmux server that was started from a process holding no
lock descriptor. While the window runs, the lock must be held. Once the window
closes, the lock must be free while the server still lives.

The control starts a server from a shell that holds the lock, outside the driver's
tmux wrapper. The server keeps the lock, so the wrapper must close descriptor 8 for
every tmux call. The wrapper case is in test_driver_tmux_lock_fd.py.
"""

from __future__ import annotations

import fcntl
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from argus.exec_tempdir import exec_tempdir
from driver_checkout_support import LIB, make_checkout

SESSION = "ar-checkout-lock"


def _lock_is_free(lock: Path) -> bool:
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def _tmux(tmux: str, socket: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    return subprocess.run(
        [tmux, "-S", str(socket), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _wait_for(predicate: Callable[[], bool], seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _phase_script(work: Path, repo: Path, config: Path, out: Path) -> Path:
    script = work / "phase.sh"
    script.write_text(
        "set -u\n"
        f"REPO={shlex.quote(str(repo))}\n"
        f"PYTHON={shlex.quote(sys.executable)}\n"
        f"ARGUS_CONFIG_FILE={shlex.quote(str(config))}\n"
        f". {shlex.quote(str(LIB))}\n"
        "driver_checkout_take || exit 3\n"
        f"echo taken >{shlex.quote(str(out))}\n"
        "sleep 2\n",
        encoding="utf-8",
    )
    return script


@pytest.fixture
def tmux() -> str:
    binary = shutil.which("tmux")
    if binary is None:
        pytest.skip("tmux is needed to start the server that could keep the lock")
    return binary


def test_a_phase_window_releases_the_checkout_lock_for_the_tmux_server(
    tmux: str, tmp_path: Path
) -> None:
    repo = make_checkout(tmp_path / "repo")
    lock = repo / ".git" / "argus-driver-checkout.lock"
    lock.touch()
    scratch = exec_tempdir(purpose="a tmux server socket")
    work = Path(scratch.name)
    socket = work / "tmux.sock"
    out = work / "taken"
    config = tmp_path / "a" / "config-a.jsonc"
    try:
        # The server starts from this process, which holds no lock descriptor.
        _tmux(tmux, socket, "new-session", "-d", "-s", SESSION, "cat")
        script = _phase_script(work, repo, config, out)
        _tmux(
            tmux,
            socket,
            "new-window",
            "-d",
            "-t",
            SESSION,
            "-n",
            "phase",
            f"bash {shlex.quote(str(script))}",
        )
        assert _wait_for(out.exists), "the phase window never took the lock"
        assert not _lock_is_free(lock), "the phase did not hold the lock"
        assert _wait_for(
            lambda: (
                "phase"
                not in _tmux(
                    tmux, socket, "list-windows", "-t", SESSION, "-F", "#{window_name}"
                ).stdout
            )
        ), "the phase window did not close"
        alive = _tmux(tmux, socket, "has-session", "-t", SESSION)
        assert alive.returncode == 0, "no tmux server runs, so the check proves nothing"
        assert _lock_is_free(lock), "the tmux server keeps the checkout lock"
    finally:
        _tmux(tmux, socket, "kill-server")
        scratch.cleanup()


def test_control_a_server_started_with_the_lock_held_keeps_it(
    tmux: str, tmp_path: Path
) -> None:
    repo = make_checkout(tmp_path / "repo")
    lock = repo / ".git" / "argus-driver-checkout.lock"
    lock.touch()
    scratch = exec_tempdir(purpose="a tmux server socket")
    work = Path(scratch.name)
    socket = work / "tmux.sock"
    try:
        body = (
            f"exec 8<>{shlex.quote(str(lock))}\n"
            f"{shlex.quote(sys.executable)} -c "
            "'import fcntl; fcntl.flock(8, fcntl.LOCK_EX | fcntl.LOCK_NB)' || exit 3\n"
            f"{shlex.quote(tmux)} -S {shlex.quote(str(socket))} "
            f"new-session -d -s {SESSION} cat\n"
            "exec 8>&-\n"
        )
        subprocess.run(["bash", "-c", body], check=True, timeout=60)
        alive = _tmux(tmux, socket, "has-session", "-t", SESSION)
        assert alive.returncode == 0, (
            "no tmux server runs, so the control proves nothing"
        )
        assert not _lock_is_free(lock), (
            "a server started with the lock held did not keep it"
        )
    finally:
        _tmux(tmux, socket, "kill-server")
        scratch.cleanup()
