# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Driver claim and release fence refusal tests."""

from pathlib import Path

from driver_maintenance_support import shell


def test_record_claim_refuses_when_the_board_did_not_return_a_fence(
    tmp_path: Path,
) -> None:
    output = shell(
        tmp_path,
        'rc=0; record_claim 100020 "" "" || rc=$?; printf "claim_rc=%s\\n" "$rc"\n',
    )
    assert "claim_rc=1" in output
    assert not (tmp_path / "claim").exists()


def test_record_claim_refuses_a_foreign_fence(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "board_assert_owner() { log ASSERT; return 1; }\n"
        "rc=0; record_claim 100020 rsv-foreign 7 || rc=$?\n"
        "printf 'claim_rc=%s\\n' \"$rc\"\n",
    )
    assert "claim_rc=1" in output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert not (tmp_path / "claim").exists()


def test_session_claim_ensure_refuses_a_failed_record_claim(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        '. "$TEST_DRIVER/lib-driver-session-claim.sh"\n'
        "python_transport() { return 0; }\n"
        "PYTHON=python_transport\n"
        "PHASE_MACHINE_ID=fixture\n"
        "CLIO_RESERVATION_ID=rsv-fixture\n"
        "CLIO_RESERVATION_GENERATION=7\n"
        "record_claim() { log RECORD_CLAIM_FAILED; return 1; }\n"
        "run_session() { log LAUNCH; }\n"
        "rc=0; session_claim_ensure 100020 fixture.yml || rc=$?\n"
        "printf 'session_rc=%s\\n' \"$rc\"\n"
        'if [ "$rc" -eq 0 ]; then run_session 100020 fixture.yml; fi\n',
    )
    assert "session_rc=1" in output
    assert "LAUNCH" not in (tmp_path / "events").read_text().splitlines()


def test_abandon_refuses_a_foreign_fence_before_writing_residue(
    tmp_path: Path,
) -> None:
    output = shell(
        tmp_path,
        '. "$TEST_DRIVER/lib-driver-abandon.sh"\n'
        'mkdir -p "$RUN_DIR/phase-100020"\n'
        'phase_run_dir() { printf "%s\\n" "$RUN_DIR/phase-100020"; }\n'
        "resume_phase_file_of() { printf fixture.yml; }\n"
        "phase_state() { printf running; }\n"
        "tmux_busy() { return 1; }\n"
        "driver_orphan_check() { return 0; }\n"
        "driver_lock_take() { :; }\n"
        "board_assert_owner() { log ASSERT; return 1; }\n"
        'printf "100020\\trsv-foreign\\t7\\n" >"$CLAIM"\n'
        'rc=0; (abandon_cmd 100020) >"$RUN_DIR/abandon-output" 2>&1 || rc=$?\n'
        'printf "abandon_rc=%s\\n" "$rc"\n',
    )
    assert "abandon_rc=1" in output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert not (tmp_path / "phase-100020" / "abandoned.json").exists()
    assert (tmp_path / "claim").is_file()


def test_release_refuses_when_the_claim_fence_is_missing(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "driver_lock_wait() { :; }\n"
        "rc=0; release || rc=$?\n"
        "printf 'release_rc=%s\\n' \"$rc\"\n",
    )
    assert "release_rc=1" in output
    assert not (tmp_path / "release-checkpoint.json").exists()


def test_release_refuses_a_foreign_fence_before_checkpoint(tmp_path: Path) -> None:
    body = (
        "printf '100020\\n' >\"$CURRENT\"\n"
        "printf '100020\\trsv-foreign\\t7\\n' >\"$CLAIM\"\n"
        "driver_lock_wait() { :; }\n"
        "board_assert_owner() { log ASSERT; return 1; }\n"
        "rc=0; release || rc=$?\n"
        "printf 'release_rc=%s\\n' \"$rc\"\n"
    )
    output = shell(tmp_path, body)
    assert "release_rc=1" in output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert not (tmp_path / "release-checkpoint.json").exists()
    assert (tmp_path / "claim").is_file()
    assert (tmp_path / "current").is_file()
