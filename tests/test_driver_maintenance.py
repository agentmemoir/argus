# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Publication settles independently of selecting and launching another phase."""

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from config_fixture import BASE, PHASES_LOCAL, write
from driver_maintenance_support import DRIVER, shell


@pytest.mark.parametrize("publication", ["open", "merged"])
def test_halted_tick_settles_publication_without_selection(
    tmp_path: Path, publication: str
) -> None:
    shell(tmp_path, f': >"$HALT"\nPUBLICATION={publication}\n_drive\n')
    events = (tmp_path / "events").read_text().splitlines()
    # The merge check runs on every tick; a halt holds the launch either way.
    assert any(event.startswith("merge check:") for event in events)
    if publication == "merged":
        assert "PUBLISH" in events
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
    assert any(event.startswith("merge check:") for event in events)
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


def test_scan_failure_continues_to_select(tmp_path: Path) -> None:
    shell(
        tmp_path,
        """
pending_review_snapshot() { return 1; }
_drive
""",
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert "SELECT" in events


def test_halted_maintenance_retries_tidy_with_no_pending_receipts(
    tmp_path: Path,
) -> None:
    shell(
        tmp_path,
        """
: >"$HALT"
pending_review_snapshot() { printf '{"awaiting_review":[]}'; }
tidy_published_checkout() { log TIDY; [ -f "$RUN_DIR/repaired" ]; }
rc=0; _drive --merge-check-only || rc=$?
printf 'first_rc=%s\\n' "$rc" >>"$RUN_DIR/events"
: >"$RUN_DIR/repaired"
_drive --merge-check-only
""",
    )
    events = (tmp_path / "events").read_text().splitlines()
    assert events.count("TIDY") == 2
    # A blocked tidy (no repaired marker) is a narrow stop; the tick that finds
    # it repaired continues.
    assert "first_rc=1" in events
    assert "SELECT" not in events


def test_idle_stop_does_not_hold_stale_current_file(tmp_path: Path) -> None:
    # A current file with no claim proves nothing, so the stop halts the line
    # but the stale current file never becomes a hold. Once the marker is
    # cleared, the next tick selects work: nothing is left for a resume to
    # consume.
    output = shell(
        tmp_path,
        'printf "100020\\n" >"$CURRENT"\nrc=0; stop || rc=$?\n'
        'printf "stop_rc=%s\\n" "$rc"\n'
        '[ -f "$HALT" ] && printf "halted=yes\\n"\nrm "$HALT"\n_drive\n',
    )
    assert "stop_rc=0" in output
    assert "halted=yes" in output
    assert not (tmp_path / "stopped-claim").exists()
    assert "SELECT" in (tmp_path / "events").read_text().splitlines()


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
_drive_resume_group() { :; }
_drive_resume_branch() { :; }
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
# The stub, so DRIVER_STUB_BOARD_FENCE below decides this case; the harness
# stub would make the refused-fence half pass for the wrong reason.
board_assert_owner() { [ "${DRIVER_STUB_BOARD_FENCE:-}" != fail ] || return 1; }
python_transport() {
  case "$1" in
    */board/reservations.py) return 0 ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
stop
DRIVER_STUB_BOARD_FENCE=BOARD_RESULT
(abandon_cmd 100020) || true
""".replace("BOARD_RESULT", "assert" if release_ok else "fail"),
    )
    assert (tmp_path / "stopped-claim").exists() is not release_ok
    assert (tmp_path / "halted").exists()
    if release_ok:
        assert (
            json.loads((tmp_path / "abandoned.json").read_text())["phase"] == "100020"
        )
    else:
        # A refused fence means no residue write: the phase is not parked.
        assert not (tmp_path / "abandoned.json").exists()


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
    config.update({"repo-root": ".", "phases": {**PHASES_LOCAL, "repo": "project"}})
    (tmp_path / "project/plans").mkdir(parents=True)
    (tmp_path / "project/plans/phase-100010-fixture.md").write_text("# Fixture\n")
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


READY_LINE = "a group's shared pull request is ready; its checks passed"


def test_a_ready_sweep_with_no_new_transition_logs_nothing(tmp_path: Path) -> None:
    # The first sweep readies the pull request. The second sweep reads READY
    # too, but only because it skipped that pull request: nothing new to log.
    answer: dict[str, Any] = {
        "ok": True,
        "mode": "ready",
        "state": "READY",
        "results": [
            {
                "ok": True,
                "phase": "100010",
                "pull_request": "https://example.test/pull/1",
                "detail": "",
                "state": "READY",
            }
        ],
    }
    (tmp_path / "fresh.json").write_text(json.dumps(answer), encoding="utf-8")
    answer["results"][0]["skipped"] = True
    (tmp_path / "skipped.json").write_text(json.dumps(answer), encoding="utf-8")
    shell(
        tmp_path,
        "SWEEP=fresh.json\n"
        "python_transport() {\n"
        '  case "$1" in\n'
        '    */gitsync/main.py) cat "$TEST_ROOT/$SWEEP" ;;\n'
        '    *) "$TEST_PYTHON" "$@" ;;\n'
        "  esac\n"
        "}\n"
        "reconcile_group_ready\n"
        "SWEEP=skipped.json\n"
        "reconcile_group_ready\n",
    )
    events = (tmp_path / "events").read_text(encoding="utf-8").splitlines()
    assert events.count(READY_LINE) == 1
