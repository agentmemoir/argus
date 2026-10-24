"""The driver's tmux windows follow the attached client and have no fixed size.

The test runs the driver's own session and phase-window functions on a real tmux
server with a private socket. It checks three things: the size options are set
to follow the client, the only size call is the one-time detached default for the
idle window, and the phase window and its pane change size when an attached client
changes size. A detached idle and phase window take the default size.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PHASE_TMUX = REPO / "src/argus/driver/phase-tmux.sh"
SESSION = "ar-size-test"
DEFAULT_SIZE = "200x50"

# The driver functions run as they do in production. `cat` stands in for the
# idle script and for the phase. Every tmux call is logged, so the test can see
# the arguments. The socket is private, so the user's own tmux server is untouched.
SNIPPET = r"""
tmux_wrap() {
  printf '%s\n' "$*" >> "$CALLS"
  command "$TMUX_REAL" -L "$SERVER" "$@"
}
CMD_TMUX=tmux_wrap
SESSION="$SESSION_NAME"
TMUX_WIDTH=200
TMUX_HEIGHT=50
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
tmux_phase_window_open cat ""
"""


def _wait_for(check: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()


def test_windows_follow_the_attached_client_and_have_no_fixed_size(
    tmp_path: Path,
) -> None:
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is needed to run a real server")
    server = f"argus-size-{os.getpid()}"
    calls = tmp_path / "calls.txt"
    calls.touch()
    env = {key: value for key, value in os.environ.items() if key != "TMUX"}
    env.update(
        CALLS=str(calls),
        SERVER=server,
        WORK=str(tmp_path),
        TMUX_REAL=tmux,
        PHASE_TMUX=str(PHASE_TMUX),
        SESSION_NAME=SESSION,
    )

    def window_size(name: str) -> str:
        out = subprocess.run(
            [
                tmux,
                "-L",
                server,
                "display",
                "-p",
                "-t",
                f"{SESSION}:{name}",
                "#{window_width}x#{window_height}",
            ],
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def pane_size(name: str) -> str:
        out = subprocess.run(
            [
                tmux,
                "-L",
                server,
                "display",
                "-p",
                "-t",
                f"{SESSION}:{name}",
                "#{pane_width}x#{pane_height}",
            ],
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    client: subprocess.Popen[str] | None = None
    try:
        run = subprocess.run(
            ["bash", "-c", SNIPPET],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert run.returncode == 0, run.stderr

        def option(name: str) -> str:
            out = subprocess.run(
                [tmux, "-L", server, "show-options", "-w", "-t", SESSION, "-v", name],
                capture_output=True,
                text=True,
            )
            return out.stdout.strip()

        assert option("window-size") == "latest"
        assert option("aggressive-resize") == "on"
        assert option("default-size") == DEFAULT_SIZE

        # The only size call allowed is the one-time detached default for the
        # idle window at session creation. No other call may pass a size.
        logged = calls.read_text()
        assert logged, "the driver made no tmux calls"
        idle_default = f"resize-window -t {SESSION}:idle -x 200 -y 50"
        for line in logged.splitlines():
            args = line.split()
            if "-x" in args or "-y" in args or "resize-window" in args:
                assert line == idle_default, line

        # Detached: the idle and phase windows take the default size.
        assert window_size("idle") == DEFAULT_SIZE
        assert window_size("phase") == DEFAULT_SIZE

        # A control client attaches and sets its size. Windows and pane follow.
        client = subprocess.Popen(
            [tmux, "-L", server, "-C", "attach", "-t", SESSION],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert client.stdin is not None
        for size in ("100x30", "140x40"):
            client.stdin.write(f"refresh-client -C {size}\n")
            client.stdin.flush()
            assert _wait_for(lambda s=size: window_size("phase") == s), size
            assert window_size("idle") == size, size
            # The pane is the window less its status row, so its width matches
            # the window and its height is at most the window height.
            width, height = (int(part) for part in size.split("x"))
            pane_width, pane_height = (
                int(part) for part in pane_size("phase").split("x")
            )
            assert pane_width == width, size
            assert 0 < pane_height <= height, size
    finally:
        if client is not None:
            client.terminate()
            client.wait(timeout=5)
        subprocess.run([tmux, "-L", server, "kill-server"], capture_output=True)
