# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Publication settles independently of selecting and launching another phase."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config_fixture import BASE, write

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def shell(tmp_path: Path, body: str) -> str:
    script = """
set -euo pipefail
for name in lib-driver-drive lib-driver-select lib-driver-group lib-driver-resume \
    lib-driver-select-flow lib-driver-issues lib-driver-config lib-driver-run; do
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


@pytest.mark.parametrize("publication", ["open", "merged"])
def test_halted_tick_settles_publication_without_selection(
    tmp_path: Path, publication: str
) -> None:
    shell(tmp_path, f': >"$HALT"\nPUBLICATION={publication}\n_drive\n')
    events = (tmp_path / "events").read_text().splitlines()
    assert ("PUBLISH" if publication == "merged" else "GATE") in events
    assert not {"SELECT", "BRANCH", "LAUNCH"}.intersection(events)
    assert (tmp_path / "halted").exists()


def test_publication_progress_ends_tick_before_selection(tmp_path: Path) -> None:
    shell(tmp_path, "PUBLICATION=merged\n_drive\n")
    events = (tmp_path / "events").read_text().splitlines()
    assert "PUBLISH" in events
    assert "SELECT" not in events


def test_merge_check_only_does_not_select(tmp_path: Path) -> None:
    shell(tmp_path, "_drive --merge-check-only\n")
    events = (tmp_path / "events").read_text().splitlines()
    assert "GATE" in events
    assert not {"SELECT", "BRANCH", "LAUNCH"}.intersection(events)


def test_stop_blocks_own_claim_after_halt_marker_is_cleared(tmp_path: Path) -> None:
    shell(
        tmp_path,
        """
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
stop
rm "$HALT"
_drive
""",
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert "SELECT" not in events
    assert (tmp_path / "stopped-claim").read_text().strip() == "100020"


def test_expired_stop_hold_is_released_and_selection_resumes(tmp_path: Path) -> None:
    shell(
        tmp_path,
        """
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
printf '100020\\n' >"$RUN_DIR/stopped-claim"
stopped_claim_expired() { return 0; }
_drive
""",
    )
    assert not (tmp_path / "stopped-claim").exists()
    assert "SELECT" in (tmp_path / "events").read_text().splitlines()


def test_active_phase_prevents_checkout_maintenance(tmp_path: Path) -> None:
    shell(tmp_path, "tmux_busy() { return 0; }\ncheck_stall() { :; }\n_drive\n")
    events = (tmp_path / "events").read_text().splitlines()
    assert not {"GATE", "PUBLISH", "SELECT", "TIDY"}.intersection(events)


def test_scan_failure_refuses_launch(tmp_path: Path) -> None:
    shell(
        tmp_path,
        """
pending_review_snapshot() { return 1; }
if _drive; then exit 7; fi
""",
    )
    assert "SELECT" not in (tmp_path / "events").read_text().splitlines()


def test_halted_maintenance_retries_tidy_with_no_pending_receipts(
    tmp_path: Path,
) -> None:
    shell(
        tmp_path,
        """
: >"$HALT"
pending_review_snapshot() { printf '{"awaiting_review":[]}'; }
tidy_published_checkout() { log TIDY; [ -f "$RUN_DIR/repaired" ]; }
if _drive --merge-check-only; then exit 7; fi
: >"$RUN_DIR/repaired"
_drive --merge-check-only
""",
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert events.count("TIDY") == 2
    assert "SELECT" not in events


def test_idle_stop_does_not_hold_stale_current_file(tmp_path: Path) -> None:
    shell(tmp_path, 'printf "100020\\n" >"$CURRENT"\nstop\nrm "$HALT"\n_drive\n')
    assert not (tmp_path / "stopped-claim").exists()
    assert "SELECT" in (tmp_path / "events").read_text().splitlines()


@pytest.mark.parametrize("transition", [True, False])
def test_group_ready_transition_ends_tick_before_selection(
    tmp_path: Path, transition: bool
) -> None:
    ready = ',"ready":{"ok":true}' if transition else ""
    shell(
        tmp_path,
        """
. "$TEST_DRIVER/lib-driver-group.sh"
claim_renew() { :; }
watch_signals_pull_request() { :; }
python_transport() {
  case "$*" in
    *'--mode ready'*) printf '%s' 'READY_ANSWER' ;;
    *'--mode published'*) printf '{"state":"BLOCKED"}' ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
_drive
""".replace("READY_ANSWER", '{"state":"READY","summary":"ready"' + ready + "}"),
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert ("SELECT" in events) is not transition


@pytest.mark.parametrize("launch", ["ok", "stop-again", "fail", "fail-new-stop"])
def test_explicit_resume_consumes_only_accepted_stop(
    tmp_path: Path, launch: str
) -> None:
    shell(
        tmp_path,
        """
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-run.sh"
printf '100020\\n' >"$RUN_DIR/stopped-claim"
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
printf 'manual halt\\n' >"$HALT"
resume_phase_file_of() { printf 'phase-100020-fixture.md'; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
_drive_resume_branch() { :; }
resume_ledger_proof() { :; }
resume_fence() { RESUME_RESERVATION_ID=rsv-fixture; RESUME_RESERVATION_GENERATION=2; }
record_claim() { :; }
resume_stage() { printf fixture; }
LAUNCH_SETUP
resume_rc=0
(resume_cmd) || resume_rc=$?
case LAUNCH_RESULT in
  fail*) test "$resume_rc" -ne 0 ;;
  *) test "$resume_rc" = 0 ;;
esac
""".replace("LAUNCH_RESULT", launch).replace(
            "LAUNCH_SETUP",
            {
                "ok": "",
                "stop-again": "_drive_launch() { log LAUNCH; stop; }",
                "fail": "_drive_launch() { _DRIVE_RC=1; }",
                "fail-new-stop": '_drive_launch() { printf "100030\\n" >"$RUN_DIR/stopped-claim"; _DRIVE_RC=1; }',
            }[launch],
        ),
    )
    assert (tmp_path / "stopped-claim").exists() is (launch != "ok")
    assert (tmp_path / "halted").exists()
    if launch == "ok":
        assert (tmp_path / "halted").read_text() == "manual halt\n"
    if not launch.startswith("fail"):
        assert "LAUNCH" in (tmp_path / "events").read_text().splitlines()
    if launch == "fail-new-stop":
        assert (tmp_path / "stopped-claim").read_text() == "100030\n"


@pytest.mark.parametrize("release_ok", [True, False])
def test_abandon_clears_manual_stop_after_group_release(
    tmp_path: Path, release_ok: bool
) -> None:
    shell(
        tmp_path,
        """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
stop
DRIVER_STUB_BOARD_FENCE=BOARD_RESULT
(abandon_cmd 100020) || true
""".replace("BOARD_RESULT", "renew" if release_ok else "fail"),
    )
    assert (tmp_path / "stopped-claim").exists() is not release_ok
    assert (tmp_path / "halted").exists()
    assert json.loads((tmp_path / "abandoned.json").read_text())["phase"] == "100020"


def test_cleared_failure_halt_still_resumes_recorded_phase(tmp_path: Path) -> None:
    shell(
        tmp_path,
        """
: >"$HALT_RECORD"
_drive_resume() { log RESUME; }
_drive
""",
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert "RESUME" in events
    assert "SELECT" not in events


def test_pending_scan_reads_pipeline_receipts_without_selecting(tmp_path: Path) -> None:
    config = copy.deepcopy(BASE)
    config.update({"repo-root": ".", "phases-dir": "plans"})
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans/phase-100010-fixture.md").write_text("# Fixture\n")
    (tmp_path / "pipeline.yaml").write_text(
        "run_dir: history/phase-{phase_number:06d}\n"
        "inputs:\n  phase_number: {type: int}\n  phase_file: {}\n"
    )
    receipt = tmp_path / "history/phase-100010/publication.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "state": "awaiting_review",
                "phase": "100010",
                "branch": "feature/fixture",
                "pull_request": "https://example.test/pull/1",
            }
        )
    )
    write(tmp_path, json.dumps(config), name="fixture.json")
    output = shell(
        tmp_path,
        '. "$TEST_DRIVER/lib-driver-maintenance.sh"\npending_review_snapshot\n',
    )
    assert json.loads(output)["awaiting_review"] == [
        {
            "phase": "100010",
            "branch": "feature/fixture",
            "pull_request": "https://example.test/pull/1",
            "head": None,
        }
    ]


def test_help_explains_maintenance_mode(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(DRIVER / "phase-driver.sh"), "--help"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    assert "--merge-check-only" in result.stdout
    assert "without selecting or launching work" in result.stdout
