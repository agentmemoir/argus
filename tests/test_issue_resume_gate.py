# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A halted issue run is never resumed while an Argus pull request owns the issue.

The resume path asks the selection gate before it launches: an open (draft or
ready) or merged Argus pull request means no resume, and a check that cannot
answer means no resume either.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

SCRIPT = """
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-resume; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"; ARGUS="$TEST_ROOT"; REPO="$TEST_ROOT"; ARGUS_CONFIG_ARGS=()
RUN_DIR="$TEST_ROOT/run"; mkdir -p "$RUN_DIR"; LOG="$RUN_DIR/log"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"; : >"$HALT_RECORD"
work_kind=issue; PHASE_FILE_INPUT=issue_file
halt_record_read() {
  HALT_REC_PHASE=270; HALT_REC_PHASE_FILE="tasks/issue-000270-fix.md"
  HALT_REC_RUN_DIR="$RUN_DIR"; HALT_REC_STATE=""; HALT_REC_RESERVATION_ID=""
  HALT_REC_GENERATION=""
  return 0
}
owns_stub() { [ "$TEST_OWNS" != fail ] || return 1; printf '%s' "$TEST_OWNS"; }
ISSUE_PR_OWNS_CMD=owns_stub
work_preflight() { echo reached-launch-path; return 1; }
resume_refuse() { return 1; }
RESUME_FROM_TICK="$TEST_FROM_TICK"
_drive_resume ""; echo "rc=$?"
if [ -e "$HALT_RECORD" ]; then echo kept; else echo removed; fi
"""


def resume(tmp_path: Path, owns: str, from_tick: str) -> str:
    return subprocess.run(
        ["bash", "-c", SCRIPT],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_OWNS": owns,
            "TEST_FROM_TICK": from_tick,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout


@pytest.mark.parametrize(
    ("owns", "from_tick", "record"),
    [("owned", "1", "removed"), ("owned", "0", "kept"), ("fail", "1", "kept")],
)
def test_a_halted_issue_owned_by_a_pull_request_is_not_resumed(
    tmp_path: Path, owns: str, from_tick: str, record: str
) -> None:
    # Given a halted issue run whose issue has an open or merged Argus pull request,
    # or whose check cannot answer, when the resume runs, then nothing launches.
    # From the tick, an owned issue's halt record is consumed, so the line goes on.
    out = resume(tmp_path, owns, from_tick)
    assert "reached-launch-path" not in out, out
    assert "rc=1" in out and record in out, out


def test_a_halted_issue_with_no_owning_pull_request_goes_on_to_the_launch_path(
    tmp_path: Path,
) -> None:
    out = resume(tmp_path, "free", "1")
    assert "reached-launch-path" in out and "kept" in out, out
