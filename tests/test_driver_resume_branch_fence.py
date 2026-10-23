# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A resume stops when the branch cut's fence refuses, and launches nothing.

``gitsync --mode branch`` proves the board fence before the cut. The resume
read any failure of it as a branch repair and went on to launch, so a fence
refusal resumed the phase anyway. A refusal must stop the resume; any other
branch failure stays a repair.
"""

from pathlib import Path

import pytest

from driver_maintenance_support import shell

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

RESUME_BRANCH = """
group_take_declared() { return 0; }
group_active() { return 1; }
phases_dir() { printf phases; }
branch_state_line() { printf 'branch line'; }
python_gitsync() {
  case "$1" in
    */gitsync/main.py) printf '%s' "$BRANCH_OUT"; return "$BRANCH_RC" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
PYTHON=python_gitsync
RESUME_REFUSAL=""
rc=0; _drive_resume_branch 100020 phases/phase-100020-cut.md || rc=$?
printf 'rc=%s refusal=%s\\n' "$rc" "$RESUME_REFUSAL"
"""


@pytest.mark.parametrize("state", ["FENCED", "COORDINATION_UNAVAILABLE"])
def test_a_fence_refusal_at_the_resume_branch_cut_stops_the_resume(
    tmp_path: Path, state: str
) -> None:
    out = f'{{"ok": false, "mode": "branch", "state": "{state}", "phase": "100020"}}'
    output = shell(tmp_path, f"BRANCH_OUT='{out}'; BRANCH_RC=1\n" + RESUME_BRANCH)
    assert "rc=1" in output
    assert f"the {state} fence refused the branch cut" in output
    events = tmp_path / "events"
    assert not events.exists() or "continuing on" not in events.read_text()


def test_a_resume_branch_failure_that_is_not_a_fence_stays_a_repair(
    tmp_path: Path,
) -> None:
    out = '{"ok": false, "mode": "branch", "state": "BLOCKED"}'
    output = shell(tmp_path, f"BRANCH_OUT='{out}'; BRANCH_RC=1\n" + RESUME_BRANCH)
    assert "rc=0 refusal=\n" in output
    assert "continuing on" in (tmp_path / "events").read_text()


def test_an_issue_resume_branch_failure_stops_the_resume(tmp_path: Path) -> None:
    out = '{"ok": false, "mode": "branch", "state": "BLOCKED"}'
    output = shell(
        tmp_path, f"work_kind=issue; BRANCH_OUT='{out}'; BRANCH_RC=1\n" + RESUME_BRANCH
    )
    assert "rc=1 refusal=could not put issue 100020 on its own branch" in output
    assert "continuing on" not in (tmp_path / "events").read_text()


def test_the_resume_fences_and_claims_before_it_cuts_a_branch() -> None:
    """The branch step proves the fence, so the fence must be down first.

    Cut before ``resume_fence``, the step read the halted run's expired
    reservation file and refused a resume that would renew or reserve.
    """
    text = (DRIVER / "lib-driver-resume.sh").read_text()
    body = text.split("_drive_resume() {", 1)[1]
    claim_at = body.index('record_claim_fence "$number"')
    cut_at = body.index('_drive_resume_branch "$number" "$rel"')
    assert claim_at < cut_at
