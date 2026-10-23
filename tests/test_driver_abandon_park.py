# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`abandon` takes a park reservation on the board before it writes the residue.

A phase with no live claim is parked without a release, so the board must
confirm the park first. A live foreign claim or a board that cannot answer
refuses the park and writes nothing. A park that stops after it is taken is
left to expire on its own, so the next tick is not trapped. The shell cases
stub the board per subcommand, as the unheld abandon cases do; the expiry case
runs on the fixture's real local git board.
"""

import datetime as _datetime
from pathlib import Path

from argus.board.core import GROUP_HEARTBEAT_WINDOW_S
from argus.board.store import ReservationStore
from driver_maintenance_support import shell
from driver_publication_fixture import DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)
from test_session_claim import add_phase

NUMBER = 100449
SCAN_NO_CLAIM = (
    '{"state": "OK", "phases": [{"phase": "100020", "machine_id": null, '
    '"status": null, "reason": "no_claim"}]}'
)
TAKEN = (
    'printf \'%s\' \'{"state": "OK", "reservation": '
    '{"reservation_id": "rsv-park", "generation": 1}}\''
)
FOREIGN = (
    'printf \'%s\' \'{"state": "FENCED", "error": "phase 100020 is already '
    "reserved\"}'; return 1"
)
UNREACHABLE = "return 2"

PREAMBLE = """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
board_assert_owner() { log ASSERT; return 1; }
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
"""


def _board(reserve: str, residue: str = "") -> str:
    """The abandon call with a scan that finds no claim and a stubbed reserve."""
    return (
        PREAMBLE
        + f"""
python_transport() {{
  case "$1" in
    */board/reservations.py)
      log "BOARD $2 ${{*:3}}"
      case "$2" in
        scan) printf '%s' '{SCAN_NO_CLAIM}' ;;
        reserve) {reserve} ;;
        *) return 2 ;;
      esac ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}}
{residue}
rc=0
(abandon_cmd 100020) 2>"$RUN_DIR/stderr" || rc=$?
printf 'abandon_rc=%s\\n' "$rc"
"""
    )


def _events(tmp_path: Path) -> list[str]:
    return (tmp_path / "events").read_text().splitlines()


def test_a_live_foreign_claim_at_the_park_refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    output = shell(tmp_path, _board(FOREIGN))

    assert "abandon_rc=1" in output
    assert any(line.startswith("BOARD reserve") for line in _events(tmp_path))
    assert "BOARD release-group" not in _events(tmp_path)
    assert not (tmp_path / "abandoned.json").exists()
    assert not (tmp_path / "abandoned.json.pending").exists()
    assert (tmp_path / "claim").exists()
    assert "could not take a park reservation" in (tmp_path / "stderr").read_text()


def test_a_park_that_stops_after_its_reservation_is_left_to_expire(
    tmp_path: Path,
) -> None:
    # The residue write fails after the park is taken. Nothing is released, so
    # the reservation is left to expire on the board's own heartbeat window.
    output = shell(
        tmp_path,
        _board(TAKEN, residue="abandon_residue_write() { return 1; }"),
    )

    assert "abandon_rc=1" in output
    reserves = [line for line in _events(tmp_path) if line.startswith("BOARD reserve")]
    assert reserves and "--status running" in reserves[0]
    assert "BOARD release-group" not in _events(tmp_path)
    assert not (tmp_path / "abandoned.json").exists()


def test_an_unreadable_board_at_the_park_writes_nothing(tmp_path: Path) -> None:
    output = shell(tmp_path, _board(UNREACHABLE))

    assert "abandon_rc=1" in output
    assert any(line.startswith("BOARD reserve") for line in _events(tmp_path))
    assert "BOARD release-group" not in _events(tmp_path)
    assert not (tmp_path / "abandoned.json").exists()
    assert not (tmp_path / "abandoned.json.pending").exists()
    assert (tmp_path / "claim").exists()


def test_a_park_reservation_expires_on_its_own(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    add_phase(fixture, NUMBER, "park")
    store = ReservationStore(fixture.root)
    store.reserve(NUMBER, "server-01", status="running")
    record = store.read()[0]["phases"][str(NUMBER)]
    heartbeat = _datetime.datetime.fromisoformat(str(record["heartbeat_at"]))

    store.expire_stale(
        now=heartbeat + _datetime.timedelta(seconds=GROUP_HEARTBEAT_WINDOW_S + 60)
    )

    assert store.read()[0]["phases"][str(NUMBER)]["status"] == "expired"
