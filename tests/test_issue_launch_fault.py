# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""An issue launch fault halts the line, keeps the claim, and retries itself.

These run the real session path and the real tick gate with a fake clock.
"""

import os
import subprocess
import sys
from pathlib import Path

from driver_maintenance_support import shell as drive_shell

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

SETUP = """
set -euo pipefail
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-launchfault; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."; REPO="$TEST_ROOT"; WF="$TEST_ROOT"
RUN_DIR="$TEST_ROOT/run"; mkdir -p "$RUN_DIR"; touch "$RUN_DIR/go"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"
CLAIM="$RUN_DIR/claim"; CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
DRY_RUN=0; work_kind=issue; ARGUS_CONFIG_ARGS=(); PHASE_MACHINE_ID=m1
CLIO_RESERVATION_ID=r1; CLIO_RESERVATION_GENERATION=4
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
launchfault_now() { cat "$RUN_DIR/now"; }
release_refused_claim() { printf 'release=%s\\n' "$1" >>"$RUN_DIR/events"; }
notify() { printf 'notice=%s\\n' "$1" | head -1 >>"$RUN_DIR/events"; }
notify_phase() { printf 'notify=%s\\n' "$2" >>"$RUN_DIR/events"; }
gitsync_clean() { :; }
failure_residue_write() { :; }
tmux_set_title() { :; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { :; }
DRIVER_STUB_SLEEP=0; export DRIVER_STUB_SLEEP
DRIVER_STUB_WAIT_FILE="$RUN_DIR/go"
session() {   # <rc>
  DRIVER_STUB_RC="$1"; export DRIVER_STUB_RC
  ( run_session 23 tasks/issue-000023-fix.md ) || true
}
"""


def shell(tmp_path: Path, body: str) -> str:
    done = subprocess.run(
        ["bash", "-c", SETUP + body],
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
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    events = tmp_path / "run/events"
    return events.read_text(encoding="utf-8") if events.exists() else ""


def fault_file(tmp_path: Path) -> list[str]:
    return (tmp_path / "run/launch-fault").read_text(encoding="utf-8").split()


def test_a_launch_fault_halts_and_keeps_the_claim(tmp_path: Path) -> None:
    events = shell(tmp_path, 'echo 1000 >"$RUN_DIR/now"; session 2\n')
    assert "release=" not in events, events
    assert "notify=failed" not in events, events
    assert "notice=" not in events, events
    halt = (tmp_path / "run/halted").read_text(encoding="utf-8")
    assert halt.startswith("launch fault: issue 23 attempt 1"), halt
    assert (tmp_path / "run/halted.json").exists()
    assert fault_file(tmp_path) == ["23", "1", "1600", "0"]


def test_retries_wait_ten_then_twenty_minutes_and_notify_once(tmp_path: Path) -> None:
    body = 'echo 1000 >"$RUN_DIR/now"; session 2\n'
    nexts: list[str] = []
    for clock in ("1600", "2800", "4000"):
        events = shell(tmp_path, body)
        nexts.append(fault_file(tmp_path)[2])
        body = f'echo {clock} >"$RUN_DIR/now"; session 2\n'
    events = shell(tmp_path, body)
    nexts.append(fault_file(tmp_path)[2])
    assert nexts == ["1600", "2800", "4000", "5200"], nexts
    assert events.count("notice=Needs You. Issue 23 cannot start") == 1, events
    assert fault_file(tmp_path)[1] == "4"


def test_the_tick_lifts_the_halt_only_after_the_back_off(tmp_path: Path) -> None:
    shell(tmp_path, 'echo 1000 >"$RUN_DIR/now"; session 2\n')
    shell(tmp_path, 'echo 1599 >"$RUN_DIR/now"; launchfault_due\n')
    assert (tmp_path / "run/halted").exists()
    events = shell(tmp_path, 'echo 1600 >"$RUN_DIR/now"; launchfault_due\n')
    assert not (tmp_path / "run/halted").exists()
    assert (tmp_path / "run/halted.json").exists()
    assert "launch-fault: retrying issue 23 after attempt 1" in events, events


def test_the_tick_leaves_any_other_halt_alone(tmp_path: Path) -> None:
    shell(tmp_path, 'echo 1000 >"$RUN_DIR/now"; session 2\n')
    body = 'echo stopped >"$HALT"; echo 9999 >"$RUN_DIR/now"; launchfault_due\n'
    shell(tmp_path, body)
    assert (tmp_path / "run/halted").read_text(encoding="utf-8") == "stopped\n"


def test_a_run_that_starts_clears_the_fault(tmp_path: Path) -> None:
    shell(tmp_path, 'echo 1000 >"$RUN_DIR/now"; session 2\n')
    events = shell(tmp_path, 'echo 1600 >"$RUN_DIR/now"; session 0\n')
    assert not (tmp_path / "run/launch-fault").exists()
    assert "notify=completed" in events, events


def test_a_stage_failure_keeps_the_per_issue_path(tmp_path: Path) -> None:
    events = shell(tmp_path, 'echo 1000 >"$RUN_DIR/now"; session 1\n')
    assert "release=23" in events, events
    assert "notify=failed" in events, events
    assert not (tmp_path / "run/launch-fault").exists()
    assert not (tmp_path / "run/halted").exists()


RESUME = """
. "$TEST_DRIVER/lib-driver-halt-record.sh"
work_kind=issue; DRIVER_STUB_COORDINATION=1; RESUME_FROM_TICK=1
mkdir -p "$RUN_DIR/issue-000023"
halt_record_write 23 tasks/issue-000023-fix.md "$RUN_DIR/issue-000023" \\
  config_error 2 launch-fault "hook pre_run failed (exit 1)"
phase_state() { :; }
resume_fence() { RESUME_RESERVATION_ID=r1; RESUME_RESERVATION_GENERATION=4; }
record_claim_fence() { log "fence $*"; }
_drive_launch() { log "launch resume=$RESUME_MODE"; _DRIVE_RC=0; }
rc=0; _drive_resume || rc=$?
printf 'rc=%s\\n' "$rc"
"""


def test_the_retry_resumes_a_run_that_never_started_a_stage(tmp_path: Path) -> None:
    output = drive_shell(tmp_path, RESUME)
    assert "rc=0" in output, output
    events = (tmp_path / "events").read_text(encoding="utf-8")
    assert "fence 23 r1 4" in events, events
    assert "launch resume=1" in events, events
    assert "halt-clear: phase 23 resume launched" in events, events
    assert not (tmp_path / "halted.json").exists()
    assert not (tmp_path / "halted").exists()
