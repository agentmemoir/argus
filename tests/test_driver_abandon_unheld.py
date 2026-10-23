# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`abandon` parks a phase no other machine holds live, and refuses otherwise.

A halted phase whose claim window passed, or whose group was already released,
has no fence left to prove, so the board's own scan decides: no live claim by
another machine parks the phase, a live claim by another machine refuses, and a
board that cannot answer refuses. The board cases run the installed driver on
the fixture's real local git board; only the board-down case uses the shell
harness, because a real board that cannot answer is the same exit code.
"""

import datetime as _datetime
import json
from pathlib import Path

from argus.board.store import ReservationStore
from driver_maintenance_support import shell
from driver_publication_fixture import DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)
from test_session_claim import add_phase

NUMBER = 100449
FAR_FUTURE = _datetime.datetime(2999, 1, 1, tzinfo=_datetime.UTC)


def _prepare(
    fixture: DriverPublication, machine: str
) -> tuple[dict[str, object], Path]:
    """Reserve the phase for `machine`, record the claim, and leave run work."""
    add_phase(fixture, NUMBER, "unheld")
    token = ReservationStore(fixture.root).reserve(NUMBER, machine)
    (fixture.state / "claim").write_text(
        f"{NUMBER}\t{token['reservation_id']}\t{token['generation']}\n"
    )
    run_dir = fixture.private / "runs" / f"phase-{NUMBER:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text('{"state": "halted"}\n')
    return token, run_dir


def _record(fixture: DriverPublication) -> dict[str, object] | None:
    record: dict[str, object] | None = (
        ReservationStore(fixture.root).read()[0]["phases"].get(str(NUMBER))
    )
    return record


def test_an_expired_own_claim_waits_out_its_cooldown(
    driver_publication: DriverPublication,
) -> None:
    # An expiry writes a cooldown that runs past the moment it was written. The
    # board refuses a park reservation on a record still in cooldown, so the park
    # waits: nothing is written, and the claim stays for the next run.
    fixture = driver_publication
    _token, run_dir = _prepare(fixture, "server-01")
    ReservationStore(fixture.root).expire_stale(now=FAR_FUTURE)

    result = fixture.tick("abandon", str(NUMBER))

    assert result.returncode != 0
    assert "could not take a park reservation" in result.stderr
    assert not (run_dir / "abandoned.json").exists()
    assert not (run_dir / "abandoned.json.pending").exists()
    assert (fixture.state / "claim").exists()


def test_a_live_claim_by_another_machine_refuses_and_writes_nothing(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    _token, run_dir = _prepare(fixture, "other-machine")
    claim = (fixture.state / "claim").read_text()
    before = _record(fixture)

    result = fixture.tick("abandon", str(NUMBER))

    assert result.returncode != 0
    assert "another machine holds a live claim" in result.stderr
    assert sorted(p.name for p in run_dir.iterdir()) == ["run.json"]
    assert (fixture.state / "claim").read_text() == claim
    assert _record(fixture) == before


def test_a_rerun_after_a_failed_park_finishes_it(
    driver_publication: DriverPublication,
) -> None:
    # Issue 171: the first run released the group and staged the residue, then
    # its rename failed. The rerun has no fence to prove and nothing to release.
    fixture = driver_publication
    token, run_dir = _prepare(fixture, "server-01")
    ReservationStore(fixture.root).release_group(token)
    (run_dir / "abandoned.json.pending").write_text('{"phase": "staged"}\n')

    result = fixture.tick("abandon", str(NUMBER))

    assert result.returncode == 0, result.stderr
    assert json.loads((run_dir / "abandoned.json").read_text())["phase"] == str(NUMBER)
    assert not (run_dir / "abandoned.json.pending").exists()
    assert not (fixture.state / "claim").exists()


BOARD_DOWN = """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
board_assert_owner() { log ASSERT; return 1; }
python_transport() {
  case "$1" in
    */board/reservations.py) log "BOARD $2"; return 2 ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
rc=0
(abandon_cmd 100020) 2>"$RUN_DIR/stderr" || rc=$?
printf 'abandon_rc=%s\\n' "$rc"
"""


def test_a_board_that_cannot_answer_refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    output = shell(tmp_path, BOARD_DOWN)

    assert "abandon_rc=1" in output
    events = (tmp_path / "events").read_text().splitlines()
    assert "BOARD release-group" not in events
    assert "could not ask the board" in (tmp_path / "stderr").read_text()
    assert not (tmp_path / "abandoned.json").exists()
    assert not (tmp_path / "abandoned.json.pending").exists()
    assert (tmp_path / "claim").read_text().strip() == "100020\trsv-fixture\t2"
