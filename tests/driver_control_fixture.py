# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Public resume fixtures with bounded barriers at external process transports."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from argus.gitsync.git import git
from driver_publication_fixture import DRIVER, PHASE, DriverPublication


def blocked_run(fixture: DriverPublication) -> None:
    """Keep the real claim and branch, but prepare work that can be resumed."""
    assert git(fixture.private, "fetch", "-q", "origin")[0] == 0
    assert git(fixture.private, "merge", "-q", "--ff-only", "origin/main")[0] == 0
    (fixture.run / "publication.json").unlink()
    for log in fixture.run.glob("finalize-task-r*.log"):
        log.unlink()
    (fixture.run / "run.json").write_text('{"state":"blocked"}\n')
    (fixture.run / "resume.json").write_text('{"current":"implement"}\n')
    assert git(fixture.private, "add", "-A")[0] == 0
    assert git(fixture.private, "commit", "-qm", "blocked phase")[0] == 0
    assert git(fixture.private, "push", "-q", "origin", "HEAD:main")[0] == 0


def control_tmux(fixture: DriverPublication, exec_dir: Path) -> Path:
    """Record phase-window state without starting a real process or tmux server."""
    binary = exec_dir / "control-tmux"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        f"window = Path({str(fixture.state / 'transport-window')!r})\n"
        "args = sys.argv[1:]\n"
        f"with Path({str(fixture.calls)!r}).open('a') as log:\n"
        "    log.write(json.dumps(['tmux', *args]) + '\\n')\n"
        "if args[0] == 'list-windows':\n"
        "    lines = ['@1 idle'] + (['@2 phase'] if window.exists() else [])\n"
        "    print('\\n'.join(lines if '#{window_id}' in args[-1] "
        "else [line.split()[1] for line in lines]))\n"
        "elif args[0] == 'new-window':\n"
        "    window.touch()\n"
        "elif args[0] == 'send-keys':\n"
        "    window.unlink(missing_ok=True)\n"
        "elif args[0] == 'list-panes':\n"
        "    print('0')\n"
    )
    binary.chmod(0o755)
    config = json.loads(fixture.config.read_text())
    config["commands"]["tmux"] = str(binary)
    fixture.config.write_text(json.dumps(config))
    return binary


@contextmanager
def resume_barrier(
    fixture: DriverPublication,
    exec_dir: Path,
    boundary: str,
    *,
    driver: Path = DRIVER,
    modes: tuple[str, ...] = ("--resume", str(PHASE)),
) -> Generator[tuple[subprocess.Popen[str], socket.socket], None, None]:
    """Pause a real resume before its board fetch takes the fetch lock, or at tmux new-window.

    The git gate pauses at the lock's own rev-parse, which runs before the lock is
    taken, so an overlapping process can still read the board while the resume waits.
    """
    tmux = control_tmux(fixture, exec_dir)
    command = "git" if boundary == "before-consume" else "tmux"
    executable = shutil.which("git") if command == "git" else str(tmux)
    assert executable is not None
    match = "--git-common-dir" if command == "git" else "new-window"
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(20)
        wrapper = exec_dir / f"gated-{command}"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, socket, sys\nfrom pathlib import Path\n"
            f"once = Path({str(fixture.state / 'gate-used')!r})\n"
            f"if {match!r} in sys.argv[1:] and not once.exists():\n"
            "    once.touch()\n"
            f"    with socket.create_connection({server.getsockname()!r}, timeout=20) as gate:\n"
            "        gate.sendall(b'r')\n"
            "        assert gate.recv(1) == b'1', 'barrier not released'\n"
            f"os.execv({executable!r}, [{executable!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        config = json.loads(fixture.config.read_text())
        config["commands"][command] = str(wrapper)
        fixture.config.write_text(json.dumps(config))
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DRIVER_", "CLIO_", "ARGUS_"))
        }
        process = subprocess.Popen(
            [
                "bash",
                str(driver),
                "--config",
                str(fixture.config),
                *modes,
            ],
            cwd=fixture.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            start_new_session=True,
        )
        try:
            try:
                connection = server.accept()[0]
            except TimeoutError as exc:
                log = fixture.state / "driver.log"
                raise AssertionError(
                    log.read_text() if log.exists() else "no driver log"
                ) from exc
            with connection:
                connection.settimeout(20)
                assert connection.recv(1) == b"r"
                yield process, connection
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
