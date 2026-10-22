# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The checkout, claim, and stub board that the write-fence cases share."""

import json
import subprocess
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import ReservationFenced

PHASE = 100020


def run_path(root: Path, phase: int = PHASE) -> Path:
    return root / "runs" / f"phase-{phase}"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout with one commit, and the settings every mode reads."""
    monkeypatch.setattr(conventions, "_settings", {})
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README").write_text("fixture\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "fixture")
    conventions.configure(
        {"commands.git": "git", "repo-root": str(root), "driver.runs": "runs"}
    )
    return root


class Board:
    """A board that confirms the fixture reservation and fences every other one.

    The board transport is proved by ``tests/test_driver_claim_fencing.py`` and
    the board's own suite; what these cases pin is the ordering, so the fence is
    stubbed at its one hole rather than rebuilt with a bare-clone board here.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def assert_owner(self, reservation: dict[str, object]) -> dict[str, object]:
        if reservation.get("reservation_id") != "rsv-fixture":
            raise ReservationFenced(f"{PHASE} is no longer owned by this reservation")
        return dict(reservation)


def make_claim(root: Path, monkeypatch: pytest.MonkeyPatch, phase: int = PHASE) -> None:
    """The run state a live reservation leaves behind, and a board that owns it."""
    monkeypatch.setattr("argus.board.reservations.ReservationStore", Board)
    run = run_path(root, phase)
    run.mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text('{"state": "running"}\n')
    (run / "reservation.json").write_text(
        json.dumps(
            {
                "machine_id": "server-01",
                "reservation_id": "rsv-fixture",
                "generation": 2,
                "phase": f"{phase:06d}",
            }
        )
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "run state")


def foreign_claim(root: Path, phase: int = PHASE) -> None:
    """A reservation file whose id no board record carries."""
    path = run_path(root, phase) / "reservation.json"
    data = json.loads(path.read_text())
    data["reservation_id"] = "rsv-foreign"
    path.write_text(json.dumps(data))


def head_sha(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()
