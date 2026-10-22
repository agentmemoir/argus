# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`stop` always halts the line; only the stopped-claim hold is fenced.

The halt marker is this machine's own run state, so no claim, a bad claim, or a
board that cannot answer must still stop the line. The `stopped-claim` hold
names a board reservation, so it is written only when the board confirms it.
"""

from pathlib import Path

from driver_maintenance_support import shell

STOP = 'rc=0; stop || rc=$?\nprintf "stop_rc=%s\\n" "$rc"\n'


def _events(tmp_path: Path) -> list[str]:
    return (tmp_path / "events").read_text().splitlines()


def test_stop_with_a_foreign_fence_halts_but_records_no_claim(
    tmp_path: Path,
) -> None:
    output = shell(
        tmp_path,
        "printf '100020\\trsv-foreign\\t7\\n' >\"$CLAIM\"\n"
        "board_assert_owner() { log ASSERT; return 1; }\n" + STOP,
    )
    assert "stop_rc=0" in output
    assert "ASSERT" in _events(tmp_path)
    assert (tmp_path / "halted").is_file()
    assert not (tmp_path / "stopped-claim").exists()
    assert any("recorded no stopped claim" in line for line in _events(tmp_path))


def test_stop_without_a_claim_halts_an_idle_line(tmp_path: Path) -> None:
    output = shell(tmp_path, "board_assert_owner() { log ASSERT; return 0; }\n" + STOP)
    assert "stop_rc=0" in output
    assert "ASSERT" not in _events(tmp_path)
    assert (tmp_path / "halted").is_file()
    assert not (tmp_path / "stopped-claim").exists()


def test_stop_with_an_incomplete_claim_halts_and_asks_no_board(
    tmp_path: Path,
) -> None:
    output = shell(
        tmp_path,
        "printf '100020\\n' >\"$CLAIM\"\n"
        "board_assert_owner() { log ASSERT; return 0; }\n" + STOP,
    )
    assert "stop_rc=0" in output
    assert "ASSERT" not in _events(tmp_path)
    assert (tmp_path / "halted").is_file()
    assert not (tmp_path / "stopped-claim").exists()


def test_stop_writes_both_files_when_the_fence_matches(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "printf '100020\\trsv-fixture\\t2\\n' >\"$CLAIM\"\n"
        "board_assert_owner() { log ASSERT; return 0; }\n" + STOP,
    )
    assert "stop_rc=0" in output
    assert "ASSERT" in _events(tmp_path)
    assert (tmp_path / "halted").is_file()
    assert (tmp_path / "stopped-claim").read_text().strip() == "100020"
