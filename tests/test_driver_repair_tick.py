# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Argus reads glide's result.json and treats repair_pending as a no-launch tick.

Glide returns EXIT_OK on a repair tick and writes no run.json; the state, step,
and reason live in result.json. This runs the real session path with that result
and asserts the no-launch repair-pending tick and its reason.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def run_session(tmp_path: Path, result: dict[str, object] | None) -> str:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "go").touch()
    if result is not None:
        (run_dir / "result.json").write_text(json.dumps(result) + "\n")
    # A small synthetic run: the saved signal with its nonce, the task snapshot
    # that carries the same nonce, and the visit count that must not change.
    (run_dir / "developer-task-r1.log").write_text(
        "work in progress\nDEVELOPER_DONE nonce123\n"
    )
    (run_dir / "developer-task-r1.md").write_text("# Task\nnonce123\n")
    (run_dir / "resume.json").write_text(
        json.dumps({"current": "developer", "outputs": {}, "visits": {"developer": 1}})
        + "\n"
    )
    script = """
set -euo pipefail
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-launchfault; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."; REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT/run"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"
CLAIM="$RUN_DIR/claim"; CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
DRY_RUN=0; work_kind=phase
ARGUS_CONFIG_ARGS=()
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
notify_phase() { printf 'notify=%s\\n' "${3:-$2}" >>"$RUN_DIR/events"; }
gitsync_clean() { printf 'finalized\\n' >>"$RUN_DIR/events"; }
tmux_set_title() { :; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf ''; }
DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=0; export DRIVER_STUB_RC DRIVER_STUB_SLEEP
DRIVER_STUB_WAIT_FILE="$RUN_DIR/go"
rc=0
( run_session 100010 phase.md ) || rc=$?
printf 'rc=%s\\n' "$rc"
printf 'halt=%s\\n' "$([ -f "$HALT" ] && echo present || echo absent)"
printf 'record=%s\\n' "$([ -f "$HALT_RECORD" ] && echo present || echo absent)"
"""
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_a_pending_repair_logs_its_reason_and_launches_nothing(
    tmp_path: Path,
) -> None:
    out = run_session(
        tmp_path,
        {
            "state": "repair_pending",
            "step": "developer",
            "next": "developer",
            "reason": "boom",
            "via": "auto-repair",
            "repaired_at": "2026-10-19T00:00:00Z",
        },
    )
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    assert "repair-pending: phase 100010: developer: boom" in events, events
    assert "completed" not in events, events
    assert "notify=completed" not in events, events
    assert "finalized" not in events, events
    assert "rc=0" in out, out
    assert "halt=absent" in out, out
    assert "record=absent" in out, out
    resume = json.loads((tmp_path / "run/resume.json").read_text())
    assert resume["visits"]["developer"] == 1, resume


def test_tick_two_after_the_repair_completes_the_run(tmp_path: Path) -> None:
    out = run_session(tmp_path, None)
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    assert "repair-pending" not in events, events
    assert "finalized" in events, events
    assert "notify=completed" in events, events
    assert "rc=0" in out, out
