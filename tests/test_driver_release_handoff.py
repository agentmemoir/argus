# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Release hands partial work to the next owner under its proved claim."""

import json
from pathlib import Path

import pytest

from argus.board.reservations import ReservationStore
from argus.gitsync.git import git
from driver_publication_fixture import DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)
from test_session_claim import add_phase

NUMBER = 100448


def _prepare(fixture: DriverPublication) -> Path:
    add_phase(fixture, NUMBER, "handoff")
    token = ReservationStore(fixture.root).reserve(NUMBER, "server-01")
    (fixture.state / "claim").write_text(
        f"{NUMBER}\t{token['reservation_id']}\t{token['generation']}\n"
    )
    (fixture.state / "current").write_text(f"{NUMBER}\n")
    run = fixture.private / "runs" / f"phase-{NUMBER}"
    run.mkdir()
    (run / "run.json").write_text('{"state":"running"}\n')
    (fixture.root / "unfinished.txt").write_text("partial work\n")
    return run


@pytest.mark.parametrize("saved", ["missing", "stale"])
def test_release_refreshes_the_proved_claim_before_checkpoint(
    driver_publication: DriverPublication, saved: str
) -> None:
    fixture = driver_publication
    run = _prepare(fixture)
    if saved == "stale":
        (run / "reservation.json").write_text(
            json.dumps({"machine_id": "old", "reservation_id": "old", "generation": 1})
        )

    result = fixture.tick("--release")

    assert result.returncode == 0, result.stderr
    assert git(fixture.root, "show", f"origin/{fixture.branch}:unfinished.txt")[1] == (
        "partial work"
    )
    assert str(NUMBER) not in ReservationStore(fixture.root).read()[0]["phases"]
    assert not (fixture.state / "claim").exists()


def test_release_keeps_claim_when_the_phase_fence_cannot_be_saved(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    run = _prepare(fixture)
    (run / "reservation.json").mkdir()
    before = ReservationStore(fixture.root).read()

    result = fixture.tick("--release")

    assert result.returncode != 0
    assert ReservationStore(fixture.root).read() == before
    assert (fixture.state / "claim").is_file()
    assert (fixture.root / "unfinished.txt").is_file()
    assert not (fixture.state / "release-checkpoint.json").exists()
