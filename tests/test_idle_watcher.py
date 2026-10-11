# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The idle watcher ends when the process that started it is gone."""

import copy
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from config_fixture import BASE, write

SCRIPT = Path(__file__).resolve().parents[1] / "src/argus/driver/phase-tmux-idle.sh"
# A failure bound, not a speed claim: the watcher redraws every second.
DEADLINE = 15.0


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
