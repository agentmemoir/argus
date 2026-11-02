# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""With recover-own-claim false, a completed issue run still raises its one notice.

The real selection branch, run directory lookup, run state read, and hold run in
bash. The stubs stand in for the notice channel, the configured run folder, and the
pull request verdict.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
LIBS = (
    "lib-driver-log.sh",
    "lib-driver-select.sh",
    "lib-driver-issues.sh",
    "lib-driver-config.sh",
    "lib-driver-group.sh",
    "lib-driver-select-flow.sh",
    "lib-driver-select-real.sh",
    "lib-driver-maintenance.sh",
    "lib-driver-run.sh",
    "lib-driver-job.sh",
)
OLD_TEXT = (
    "Needs You. Issue #23 finished its run with no pull request. Argus starts no new "
    "run for it. Action: remove the claim label to retry."
)
TASK = "tasks/issue-000023-fix-the-input.md"
SETUP = """
PYTHON="$TEST_PYTHON"; ARGUS="$TEST_DRIVER/.."; REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT"; LOG="$TEST_ROOT/driver.log"
ARGUS_CONFIG_ARGS=(--config fixture.json); BOARD_KIND_ARGS=()
DRY_RUN=0; WORK_SOURCE=issues; PHASE_MACHINE_ID=fixture-machine
RECOVER_OWN_CLAIM=0; DRIVER_STUB_COORDINATION=fixture; SESSION=fixture
_DRIVE_RC=""; out=""; rc=0; selection_rc=0; number=""
notify() { printf '%s\\n' "$1" >>"$TEST_ROOT/notes.txt"; }
notify_coordination_outage() { :; }
phase_run_dir() { printf '%s' "$TEST_ROOT/run"; }
stub_verdict() {
  printf 'x' >>"$TEST_ROOT/verdicts.txt"
  [ "$TEST_VERDICT" != fail ] || return 1
  printf '%s' "$TEST_VERDICT"
}
ISSUE_PR_VERDICT_CMD=stub_verdict
_drive_stub_selection() {
  selection="$(cat "$TEST_ROOT/selection.json")"
  selection_state="$(printf '%s' "$selection" | selection_value state)"
}
"""


def tick(
    tmp_path: Path,
    run_state: str | None,
    *,
    verdict: str = "none",
    owner: str = "fixture-machine",
    ticks: int = 1,
) -> tuple[list[str], str]:
    """Run real WAIT_FOR_CLAIM ticks; return the notices and the last tick's result."""
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    if run_state is not None:
        (run / "run.json").write_text(json.dumps({"state": run_state}), "utf-8")
    selection: dict[str, object] = {
        "kind": "issue",
        "state": "WAIT_FOR_CLAIM",
        "issue": "23",
        "path": TASK,
        "owner": owner,
        "status": "claimed",
        "skipped": {},
    }
    (tmp_path / "selection.json").write_text(json.dumps(selection), "utf-8")
    sources = "\n".join(f'. "$TEST_DRIVER/{name}"' for name in LIBS)
    one = '_DRIVE_RC=""; number=""; _drive_choose_selection\n'
    report = 'printf "rc=%s number=%s" "$_DRIVE_RC" "$number"\n'
    done = subprocess.run(
        ["bash", "-u", "-c", sources + SETUP + one * ticks + report],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_VERDICT": verdict,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    notes = tmp_path / "notes.txt"
    lines = notes.read_text("utf-8").splitlines() if notes.exists() else []
    return lines, done.stdout


def test_a_completed_run_raises_one_notice_and_launches_nothing(
    tmp_path: Path,
) -> None:
    notices, result = tick(tmp_path, "completed")
    assert notices == [OLD_TEXT]
    assert result == "rc=0 number="
    assert "no launch" in (tmp_path / "driver.log").read_text("utf-8")


def test_a_second_tick_raises_no_second_notice(tmp_path: Path) -> None:
    notices, result = tick(tmp_path, "completed", ticks=2)
    assert notices == [OLD_TEXT]
    assert result == "rc=0 number="


def test_an_unknown_verdict_raises_nothing(tmp_path: Path) -> None:
    notices, result = tick(tmp_path, "completed", verdict="fail")
    assert notices == []
    assert result == "rc=0 number="


def test_a_pull_request_verdict_raises_nothing(tmp_path: Path) -> None:
    notices, _ = tick(tmp_path, "completed", verdict="pull_request")
    assert notices == []


def test_an_active_run_raises_nothing_and_reads_no_verdict(tmp_path: Path) -> None:
    notices, result = tick(tmp_path, "running")
    assert notices == []
    assert result == "rc=0 number="
    assert not (tmp_path / "verdicts.txt").exists()


def test_a_run_with_no_state_raises_nothing(tmp_path: Path) -> None:
    notices, _ = tick(tmp_path, None)
    assert notices == []


def test_another_machines_claim_raises_nothing(tmp_path: Path) -> None:
    notices, _ = tick(tmp_path, "completed", owner="other-machine")
    assert notices == []
    assert not (tmp_path / "verdicts.txt").exists()
