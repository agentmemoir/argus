"""A tmux server must not keep the driver lock after the driver exits.

The driver holds its lock on descriptor 9. A tmux server started while that
descriptor is open inherits it, so the lock stays held for as long as the server
lives, and every later tick stops at the lock check.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PHASE_TMUX = REPO / "src/argus/driver/phase-tmux.sh"
SESSION = "ar-lock-fd-test"

# The snippet takes the lock on fd 9 and then calls the real session-ensure path
# with no close on fd 9 at the call site. The function must close it for tmux.
# `cat` stands in for the idle script, so the session lives until the test ends.
SNIPPET = r"""
exec 9>"$LOCK"
"$PY" -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' || exit 3
tmux_wrap() { command "$TMUX_REAL" -S "$SOCK" "$@"; }
CMD_TMUX=tmux_wrap
SESSION="$SESSION_NAME"
TMUX_WIDTH=80
TMUX_HEIGHT=24
REPO="$WORK"
RUN_DIR="$WORK"
LOG="$WORK/driver.log"
ARGUS="$WORK"
ARGUS_DRIVER=/bin
TMUX_IDLE_SCRIPT=cat
ARGUS_CONFIG_ARGS=()
log() { :; }
. "$PHASE_TMUX"
tmux_session_ensure
"""


def _lock_is_free(lock: Path) -> bool:
    """True when a new open file description can take the lock right now."""
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def test_session_ensure_leaves_the_driver_lock_free() -> None:
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is needed to start the server that could keep the lock")
    with tempfile.TemporaryDirectory(prefix="ag-tl-") as work_name:
        work = Path(work_name)
        lock = work / "driver.lock"
        socket = work / "tmux.sock"
        env = {key: value for key, value in os.environ.items() if key != "TMUX"}
        env.update(
            LOCK=str(lock),
            SOCK=str(socket),
            WORK=work_name,
            TMUX_REAL=tmux,
            PY=sys.executable,
            PHASE_TMUX=str(PHASE_TMUX),
            SESSION_NAME=SESSION,
        )
        try:
            run = subprocess.run(
                ["bash", "-c", SNIPPET],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert run.returncode == 0, run.stderr
            alive = subprocess.run(
                [tmux, "-S", str(socket), "has-session", "-t", SESSION],
                capture_output=True,
            )
            assert alive.returncode == 0, (
                "no tmux server runs, so the check proves nothing"
            )
            assert _lock_is_free(lock), (
                "a tmux server started by the driver keeps the lock"
            )
        finally:
            subprocess.run(
                [tmux, "-S", str(socket), "kill-server"], capture_output=True
            )
