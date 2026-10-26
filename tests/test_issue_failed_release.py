# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed or escalated issue run gives its claim back, and says so honestly.

These run the real session path, so deleting either release call fails a test.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.notify import notifications
from argus.notify.notifications import format_notification
from config_fixture import write
from config_issue_fixture import issue_document

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def run_issue_session(tmp_path: Path, state: str, released: bool) -> str:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "go").touch()
    script = """
set -euo pipefail
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-launchfault; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."; REPO="$TEST_ROOT"; WF="$TEST_ROOT"
RUN_DIR="$TEST_ROOT/run"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"
CLAIM="$RUN_DIR/claim"; CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
DRY_RUN=0; work_kind=issue; ARGUS_CONFIG_ARGS=(); PHASE_MACHINE_ID=m1
CLIO_RESERVATION_ID=r1; CLIO_RESERVATION_GENERATION=4
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
release_refused_claim() {
  printf 'release=%s %s %s %s\\n' "$@" >>"$RUN_DIR/events"
  CLAIM_RELEASED="$TEST_RELEASED"
}
notify_phase() {
  printf 'notify=%s held=%s\\n' "$2" "${ISSUE_CLAIM_HELD:-}" >>"$RUN_DIR/events"
}
gitsync_clean() { :; }
failure_residue_write() { :; }
tmux_set_title() { :; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf '%s' "$TEST_STATE"; }
DRIVER_STUB_RC=1; DRIVER_STUB_SLEEP=0; export DRIVER_STUB_RC DRIVER_STUB_SLEEP
DRIVER_STUB_WAIT_FILE="$RUN_DIR/go"
( run_session 23 tasks/issue-000023-fix.md ) || echo "session rc=$?" >&2
"""
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_STATE": state,
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
    events = run_dir / "events"
    assert events.exists(), completed.stderr
    return events.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("state", "event"), [("failed", "failed"), ("remedy_escalated", "escalated")]
)
def test_an_issue_run_that_ends_badly_releases_its_claim(
    state: str, event: str, tmp_path: Path
) -> None:
    events = run_issue_session(tmp_path, state, released=True)
    assert "release=23 r1 4 failed" in events, events
    assert f"notify={event} held=0" in events, events
    assert events.index("release=") < events.index(f"notify={event}"), events


def test_a_failed_release_marks_the_claim_held(tmp_path: Path) -> None:
    events = run_issue_session(tmp_path, "failed", released=False)
    assert "notify=failed held=1" in events, events


def notice(event: str, tmp_path: Path, held: bool) -> str:
    return format_notification(
        "issue-000023-fix-api.md",
        event,
        kind="issue",
        run_json=tmp_path / "run.json",
        claim_label="taken",
        claim_held=held,
    )


@pytest.mark.parametrize("event", ["failed", "escalated"])
def test_a_held_claim_notice_does_not_promise_a_label_retry(
    event: str, tmp_path: Path
) -> None:
    released = notice(event, tmp_path, held=False)
    held = notice(event, tmp_path, held=True)
    assert "Remove the `taken` label from issue #23 to let Argus retry." in released
    assert "label from issue" not in held
    assert "could not give back its board claim" in held
    assert "even with the claim label" in held


def test_main_passes_the_configured_claim_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = issue_document()
    document["issues"]["claim-label"] = "taken"
    config = write(tmp_path, json.dumps(document))
    (tmp_path / "pipeline.yaml").write_text(
        "run_dir: runs/phase-{phase_number:06d}\n"
        "inputs:\n  phase_number: {type: int}\n  phase_file: {}\n"
    )
    args = ["--config", str(config), "--phase-file", "issue-000023-fix-api.md"]
    args += ["--event", "failed", "--kind", "issue"]
    assert notifications.main(args) == 0
    assert "Remove the `taken` label from issue #23" in capsys.readouterr().out
    assert notifications.main([*args, "--claim-held"]) == 0
    out = capsys.readouterr().out
    assert "could not give back its board claim" in out
    assert "taken" not in out
