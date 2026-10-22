# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The tick stops when the branch cut's fence refuses, and launches nothing.

``gitsync --mode branch`` proves the board fence before the cut. Its one tick
caller read any non-zero exit as a branch repair and launched on the
integration branch, so a fence refusal ran the phase anyway. A refusal must
stop the tick; any other branch failure stays a repair.
"""

from pathlib import Path

import pytest

from driver_maintenance_support import shell

CUT = """
. "$TEST_DRIVER/lib-driver-drive.sh"
gitsync_land_leftovers() { :; }
phases_dir() { printf phases; }
branch_state_line() { printf 'branch line'; }
group_active() { return 1; }
gitsync() {
  case " $* " in
    *" --mode branch "*) printf '%s' "$BRANCH_OUT"; return "$BRANCH_RC" ;;
    *" --mode resync "*) log RESYNC; printf '{"ok": true}' ;;
  esac
}
PYTHON=python_gitsync
python_gitsync() {
  case "$1" in
    */gitsync/main.py) shift; gitsync "$@" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
number=100020; rel=phases/phase-100020-cut.md; _DRIVE_RC=""
rc=0; _drive_cut_branch || rc=$?
printf 'rc=%s drive_rc=%s\\n' "$rc" "$_DRIVE_RC"
"""


@pytest.mark.parametrize("state", ["FENCED", "COORDINATION_UNAVAILABLE"])
def test_a_fence_refusal_at_the_branch_cut_stops_the_tick(
    tmp_path: Path, state: str
) -> None:
    out = f'{{"ok": false, "mode": "branch", "state": "{state}", "phase": "100020"}}'
    output = shell(tmp_path, f"BRANCH_OUT='{out}'; BRANCH_RC=1\n" + CUT)
    events = (tmp_path / "events").read_text()
    assert "drive_rc=1" in output
    assert f"the {state} fence refused the branch cut; no launch" in events
    assert "RESYNC" not in events
    assert "continuing on" not in events


def test_a_branch_failure_that_is_not_a_fence_stays_a_repair(
    tmp_path: Path,
) -> None:
    out = '{"ok": false, "mode": "branch", "state": "BLOCKED"}'
    output = shell(tmp_path, f"BRANCH_OUT='{out}'; BRANCH_RC=1\n" + CUT)
    events = (tmp_path / "events").read_text()
    assert "rc=0 drive_rc=\n" in output
    assert "continuing on" in events
    assert "RESYNC" in events
