# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared shell harness for driver maintenance tests."""

import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def shell(tmp_path: Path, body: str) -> str:
    script = """
set -euo pipefail
for name in lib-driver-drive lib-driver-control lib-driver-select lib-driver-group lib-driver-resume \
    lib-driver-select-flow lib-driver-issues lib-driver-config lib-driver-run lib-driver-fence \
    lib-driver-repair; do
  . "$TEST_DRIVER/$name.sh"
done
if [ -f "$TEST_DRIVER/lib-driver-maintenance.sh" ]; then
  . "$TEST_DRIVER/lib-driver-maintenance.sh"
fi
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."
REPO="$TEST_ROOT"; RUN_DIR="$TEST_ROOT"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"
CLAIM="$RUN_DIR/claim"; CURRENT="$RUN_DIR/current"
PIDFILE="$RUN_DIR/pid"; STALL="$RUN_DIR/stall"; LOCK="$RUN_DIR/lock"
GROUPFILE="$RUN_DIR/group"; GROUPBRANCH="$RUN_DIR/group-branch"
DRY_RUN=0; MERGE_CHECK_MAX=5; work_kind=phase; WORK_SOURCE=phases
ARGUS_CONFIG_ARGS=(--config fixture.json); GITSYNC_KIND_ARGS=(); WORK_SOURCE_FLAGS=()
PHASE_MACHINE_ID=fixture; SESSION=fixture
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
log_changed() { shift; log "$*"; }
ts() { printf fixture-time; }
notify() { :; }
tmux_busy() { return 1; }
tmux_phase_window_alive() { return 1; }
tmux_session_exists() { return 1; }
tmux_set_title() { :; }
claim_renew() { :; }
# The shell harness has no board. A test that asserts a fence refusal overrides
# this with its own stub; every other stop in these cases is an owned one.
board_assert_owner() { return 0; }
prune() { :; }
repair_due() { return 1; }
work_preflight() { return 0; }
_drive_choose_selection() { log SELECT; _DRIVE_RC=0; }
_drive_cut_branch() { log BRANCH; }
_drive_launch() { log LAUNCH; }
tidy_published_checkout() { log TIDY; }
pending_review_snapshot() {
  printf '{"awaiting_review":[{"phase":"100010","branch":"feature/fixture","pull_request":"https://example.test/pull/1"}]}'
}
gate_group_pull_request() { log GATE; }
watch_signals_pull_request() { log SIGNALS; }
python_transport() {
  case "$1" in
    */gitsync/main.py)
      if [ "$PUBLICATION" = merged ]; then
        log PUBLISH
        printf '{"state":"PUBLISHED"}'
      else
        printf '{"state":"BLOCKED"}'
      fi
      ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
PYTHON=python_transport
PUBLICATION=open
"""
    result = subprocess.run(
        ["bash", "-c", script + body],
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
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout
