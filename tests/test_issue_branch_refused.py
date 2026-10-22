# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""An issue whose branch cannot be cut never runs on the base branch.

This runs the real branch-cut path with a publishing tool that refuses, so
deleting the issue stop lets the resync and the launch run, and fails a test.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.notify.notifications import format_notification

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

REFUSING_TOOL = """import json, sys
mode = sys.argv[sys.argv.index("--mode") + 1]
with open(sys.argv[0] + ".calls", "a", encoding="utf-8") as calls:
    calls.write(mode + "\\n")
print(json.dumps({"ok": False, "state": "CONFIG_ERROR",
                  "error": "issue run directory must be inside the checkout"}))
raise SystemExit(2)
"""


def cut_branch(tmp_path: Path, kind: str, released: bool) -> tuple[str, str]:
    tool = tmp_path / "argus/gitsync/main.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(REFUSING_TOOL, encoding="utf-8")
    script = """
set -euo pipefail
for name in lib-driver-log lib-driver-select lib-driver-drive; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"; ARGUS="$TEST_ROOT/argus"; REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT/run"; mkdir -p "$RUN_DIR"
work_kind="$TEST_KIND"; ARGUS_CONFIG_ARGS=(); GITSYNC_KIND_ARGS=()
number=23; rel=tasks/issue-000023-fix.md
reservation_id=r1; reservation_generation=4
log() { printf 'log=%s\\n' "$*" >>"$RUN_DIR/events"; }
gitsync_land_leftovers() { :; }
tidy_published_checkout() { :; }
phases_dir() { :; }
group_active() { return 1; }
branch_state_line() { printf 'branch line'; }
resync_state_line() { printf 'resync line'; }
release_refused_claim() {
  printf 'release=%s %s %s %s\\n' "$@" >>"$RUN_DIR/events"
  CLAIM_RELEASED="$TEST_RELEASED"
}
notify_phase() {
  printf 'notify=%s held=%s reason=%s\\n' "$2" "${ISSUE_CLAIM_HELD:-}" "$3" \\
    >>"$RUN_DIR/events"
}
_DRIVE_RC=""
# The tick calls this behind `||`, as drive() does, so errexit is off inside.
_drive_cut_branch || printf 'cut_rc=%s\\n' "$?" >>"$RUN_DIR/events"
printf 'drive_rc=%s\\n' "$_DRIVE_RC" >>"$RUN_DIR/events"
"""
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_KIND": kind,
            "TEST_RELEASED": "1" if released else "0",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    calls = tool.with_name("main.py.calls").read_text(encoding="utf-8")
    return (tmp_path / "run/events").read_text(encoding="utf-8"), calls


def test_an_issue_branch_cut_failure_stops_the_launch(tmp_path: Path) -> None:
    events, calls = cut_branch(tmp_path, "issue", released=True)
    assert calls.split() == ["branch"], calls
    assert "drive_rc=1" in events, events
    assert "release=23 r1 4 failed" in events, events
    assert events.count("notify=") == 1, events
    assert "notify=branch-refused held=0 reason=issue run directory" in events
    assert "continuing on" not in events, events


def test_a_held_claim_is_named_in_the_notice(tmp_path: Path) -> None:
    events, _ = cut_branch(tmp_path, "issue", released=False)
    assert "notify=branch-refused held=1" in events, events


def test_a_phase_branch_cut_failure_keeps_the_repair_path(tmp_path: Path) -> None:
    events, calls = cut_branch(tmp_path, "phase", released=True)
    assert calls.split() == ["branch", "resync"], calls
    assert "drive_rc=\n" in events, events
    assert "release=" not in events and "notify=" not in events, events


@pytest.mark.parametrize("held", [False, True])
def test_the_notice_names_one_cause_and_one_action(held: bool) -> None:
    text = format_notification(
        "issue-000023-fix-api.md",
        "branch-refused",
        kind="issue",
        reason="issue run directory must be inside the checkout",
        claim_label="taken",
        claim_held=held,
    )
    assert "nothing ran on the base branch" in text
    assert "Cause: issue run directory must be inside the checkout." in text
    assert "Action: fix the cause." in text
    retry = "Remove the `taken` label from issue #23 to let Argus retry."
    assert (retry in text) is not held


def test_issues_mode_reads_no_phases_folder() -> None:
    script = '. "$1/lib-driver-config.sh"; unset PHASES_DIRECTORY; phases_dir'
    completed = subprocess.run(
        ["bash", "-uc", script, "bash", str(DRIVER)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert (completed.returncode, completed.stdout) == (0, ""), completed.stderr
