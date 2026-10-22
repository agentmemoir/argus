# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A copied reservation never proves the local machine owns a phase."""

from pathlib import Path

import pytest

from argus import conventions
from argus.board.reservations import ReservationStore
from argus.gitsync.fence import phase_fence
from argus.gitsync.git import git
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.selftest_publish import _publish_fixture

PHASE = 100020


@pytest.mark.parametrize("mode", ["checkpoint", "clean", "published"])
def test_copied_foreign_reservation_refuses_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(conventions, "_settings", {"commands.git": "git"})
    root, board, run = _publish_fixture(tmp_path, "foreign-owner", PHASE)
    conventions.override({"machine.id": "server-02"})
    before_local = git(board, "rev-parse", "HEAD")[1]
    before_board = ReservationStore(root).read()
    before_reservation = (run / "reservation.json").read_bytes()

    result = (
        publish_phase(root, PHASE)
        if mode == "published"
        else phase_fence(root, PHASE, mode)
    )

    assert result is not None
    assert result["state"] == "FENCED"
    assert git(board, "rev-parse", "HEAD")[1] == before_local
    assert ReservationStore(root).read() == before_board
    assert (run / "reservation.json").read_bytes() == before_reservation


@pytest.mark.parametrize("mode", ["checkpoint", "clean"])
def test_missing_local_identity_refuses_even_with_valid_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(conventions, "_settings", {"commands.git": "git"})
    root, _board, _run = _publish_fixture(tmp_path, "missing-identity", PHASE)
    settings = conventions.override({})
    del settings["machine.id"]
    conventions.configure(settings)

    result = phase_fence(root, PHASE, mode)

    assert result is not None
    assert result["state"] == "COORDINATION_UNAVAILABLE"
