# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The idle watcher ends when the process that started it is gone.

A session the self-test made is also named after the run that owns it, and then
the watcher ends that session itself once the run is gone. SIGKILL runs no
teardown, so without that the monitor would redraw for as long as the session
lives, which is for ever.
"""

import copy
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from config_fixture import BASE, write

SCRIPT = Path(__file__).resolve().parents[1] / "src/argus/driver/phase-tmux-idle.sh"
# A failure bound, not a speed claim: the watcher redraws every second.
DEADLINE = 15.0

#: A pid no process can hold, so a session named after it reads as abandoned.
DEAD_PID = 999999999

#: The short base a tmux socket needs: a unix socket path is capped at about 100
#: bytes and the path under `tmp_path` is longer than that.
SHORT_TMP = "/tmp"


def _short_tmp() -> str:
    """The short base a tmux socket needs, or a skip naming why there is none."""
    candidate = Path(SHORT_TMP)
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return SHORT_TMP
    pytest.skip(f"{SHORT_TMP} cannot hold a unix socket on this host")


def _tmux_env(socket: str) -> dict[str, str]:
    """This process's environment, pointed at one socket base.

    ``TMUX`` is dropped so a test run inside tmux cannot aim these commands at the
    developer's own server.
    """
    env = {name: value for name, value in os.environ.items() if name != "TMUX"}
    env["TMUX_TMPDIR"] = socket
    return env


def _sessions(binary: str, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        [binary, "list-sessions", "-F", "#{session_name}"],
        env=env,
        capture_output=True,
        text=True,
    )
    return result.stdout.split()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(condition: Callable[[], bool], what: str) -> None:
    end = time.monotonic() + DEADLINE
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_idle_watcher_exits_when_its_starter_is_killed(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    document = copy.deepcopy(BASE)
    document["tmux"]["refresh"] = 1
    config = write(tmp_path, json.dumps(document))
    screen = tmp_path / "idle.out"
    starter = subprocess.Popen(
        [
            "bash",
            "-c",
            'bash "$0" --config "$1" >"$2" 2>&1 & echo $!; wait',
            str(SCRIPT),
            str(config),
            str(screen),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert starter.stdout is not None
    watcher = int(starter.stdout.readline())
    try:
        wait_until(
            lambda: screen.exists() and "unattended line" in screen.read_text("utf-8"),
            "the watcher to draw its first screen",
        )
        starter.kill()
        starter.wait()
        wait_until(lambda: not alive(watcher), "the watcher to end after its starter")
    finally:
        if alive(watcher):
            os.kill(watcher, signal.SIGKILL)
        starter.stdout.close()


def test_the_watcher_ends_a_session_whose_run_is_gone(tmp_path: Path) -> None:
    """A session named after a dead run ends itself, monitor and all.

    The run that owns a self-test session is named in the session, so the watcher
    can tell that the run is gone without being told. A run killed with SIGKILL
    never reaches its own teardown, and this is what stops the monitor that
    teardown would have stopped.
    """
    binary = shutil.which("tmux")
    if binary is None:
        pytest.skip("tmux is needed to check that the session ends itself")
    (tmp_path / "project").mkdir()
    document = copy.deepcopy(BASE)
    document["tmux"]["refresh"] = 1
    config = write(tmp_path, json.dumps(document))
    with tempfile.TemporaryDirectory(prefix="ag-idle-", dir=_short_tmp()) as socket:
        env = _tmux_env(socket)
        subprocess.run(
            [
                binary,
                "new-session",
                "-d",
                "-s",
                f"ar-driver-selftest-{DEAD_PID}",
                f'bash "{SCRIPT}" --config "{config}"',
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            wait_until(
                lambda: not _sessions(binary, env),
                "the session to end itself once its run is gone",
            )
        finally:
            subprocess.run([binary, "kill-server"], env=env, capture_output=True)
