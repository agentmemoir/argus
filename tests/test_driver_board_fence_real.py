# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`abandon` and `--resume` prove the fence on a real board, not a stub.

Each case runs the installed driver against the fixture's local git board, so
the board calls are the real `reservations.py` commands. Only `gh` and `tmux`
are stand-ins, at the process boundary.
"""

import json
import os
import subprocess
from pathlib import Path

from argus.board.store import ReservationStore
from driver_publication_fixture import DRIVER, DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)
from test_session_claim import add_phase, windows

NUMBER = 100448


def _prepare(fixture: DriverPublication, machine: str) -> tuple[str, Path]:
    rel = add_phase(fixture, NUMBER, "fenced")
    token = ReservationStore(fixture.root).reserve(NUMBER, machine)
    (fixture.state / "claim").write_text(
        f"{NUMBER}\t{token['reservation_id']}\t{token['generation']}\n"
    )
    run_dir = fixture.private / "runs" / f"phase-{NUMBER:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if machine == "server-01":
        # The board-confirmed claim's reservation file, as record_claim writes it.
        (run_dir / "reservation.json").write_text(json.dumps(token))
    return rel, run_dir


def _resume(fixture: DriverPublication) -> subprocess.CompletedProcess[str]:
    """Run `--resume`, with the launched child stubbed as the claim tests do."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DRIVER_", "CLIO_", "ARGUS_"))
    }
    env.update(DRIVER_STUB_RC="0", DRIVER_STUB_SLEEP="0")
    return subprocess.run(
        ["bash", str(DRIVER), "--config", str(fixture.config), "--resume", str(NUMBER)],
        cwd=fixture.root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _board_record(fixture: DriverPublication) -> dict[str, object] | None:
    phases = ReservationStore(fixture.root).read()[0]["phases"]
    record: dict[str, object] | None = phases.get(str(NUMBER))
    return record


def test_abandon_with_an_owned_fence_releases_and_parks(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    _rel, run_dir = _prepare(fixture, "server-01")

    result = fixture.tick("abandon", str(NUMBER))

    assert result.returncode == 0, result.stderr
    residue = json.loads((run_dir / "abandoned.json").read_text())
    assert residue["phase"] == str(NUMBER)
    assert not (run_dir / "abandoned.json.pending").exists()
    assert not (fixture.state / "claim").exists()
    assert _board_record(fixture) is None


def test_abandon_with_another_machines_fence_writes_nothing(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    _rel, run_dir = _prepare(fixture, "other-machine")
    claim = (fixture.state / "claim").read_text()
    before = _board_record(fixture)

    result = fixture.tick("abandon", str(NUMBER))

    assert result.returncode != 0
    assert "does not confirm the fence" in result.stderr
    assert list(run_dir.iterdir()) == []
    assert (fixture.state / "claim").read_text() == claim
    assert _board_record(fixture) == before


def test_resume_with_an_owned_fence_passes_the_real_fence(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    _rel, _run_dir = _prepare(fixture, "server-01")
    claim = (fixture.state / "claim").read_text()

    result = _resume(fixture)

    # The stand-in tmux opens no window, so the resume stops at the launch. Every
    # board step before it ran for real and passed.
    assert "the launch failed" in result.stderr, result.stderr
    assert "fence" not in result.stderr
    proved = json.loads((fixture.state / f"assert-owner-{NUMBER}.json").read_text())
    assert proved["reservation"]["machine_id"] == "server-01"
    assert (fixture.state / "claim").read_text() == claim
    record = _board_record(fixture)
    assert record is not None
    assert record["machine_id"] == "server-01"
    assert record["status"] == "running"


def test_resume_with_another_machines_fence_refuses_and_writes_nothing(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    _rel, run_dir = _prepare(fixture, "other-machine")
    claim = (fixture.state / "claim").read_text()
    before_board = _board_record(fixture)
    before_windows = len(windows(fixture))

    result = _resume(fixture)

    assert result.returncode != 0
    assert "fence" in result.stderr, result.stderr
    assert len(windows(fixture)) == before_windows
    assert (fixture.state / "claim").read_text() == claim
    assert _board_record(fixture) == before_board
    assert not (run_dir / "reservation.json").exists()
