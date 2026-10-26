# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two driver configs on one repository root: the second tick refuses (issue 199).

The first config's phase holds the checkout lock. The second config's tick reaches
the branch cut with the lock held by the first, and the tick stops before the cut,
the leftover commit, and any launch. The control run has no holder, and the cut
runs. Both runs use the real tick function and stub only the git cut itself.
"""

from __future__ import annotations

from pathlib import Path

from driver_checkout_support import make_checkout, start_holder, stop_holder
from driver_maintenance_support import shell

CUT = """
. "$TEST_DRIVER/lib-driver-drive.sh"
REPO="$TEST_ROOT/repo"
gitsync_land_leftovers() { log LEFTOVERS; }
phases_dir() { printf phases; }
branch_state_line() { printf 'branch line'; }
group_active() { return 1; }
release_refused_claim() { log RELEASE; }
gitsync() { log CUT; printf '{"ok": true}'; }
PYTHON=python_gitsync
python_gitsync() {
  case "$1" in
    */gitsync/main.py) shift; gitsync "$@" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
number=100020; rel=phases/phase-100020-cut.md; _DRIVE_RC=""
reservation_id=rsv; reservation_generation=7
rc=0; _drive_cut_branch || rc=$?
printf 'rc=%s drive_rc=%s\\n' "$rc" "$_DRIVE_RC"
"""

REFUSAL = (
    "argus: checkout is in use by driver config config-a.jsonc; "
    "give each driver its own checkout"
)


def test_a_second_config_tick_refuses_before_the_cut(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    holder = start_holder(repo, tmp_path / "a" / "config-a.jsonc")
    try:
        output = shell(tmp_path, CUT)
    finally:
        stop_holder(holder)
    events = (tmp_path / "events").read_text().splitlines()
    assert "rc=0 drive_rc=1" in output, output
    assert REFUSAL in events, events
    assert "RELEASE" in events
    assert "CUT" not in events
    assert "LEFTOVERS" not in events


def test_control_without_a_holder_the_cut_runs(tmp_path: Path) -> None:
    make_checkout(tmp_path / "repo")
    shell(tmp_path, CUT)
    events = (tmp_path / "events").read_text().splitlines()
    assert "CUT" in events, events
    assert not any("checkout is in use" in line for line in events)
