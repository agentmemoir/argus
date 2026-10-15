# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A halted run is never done, and a parked phase is reported, not offered.

The approval box used to close out a blocked phase on its own, so a cleared halt
marker advanced the queue past the phase the line had stopped on. These tests pin
the guard that stops it, and the abandonment state that replaces the hand tick.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.roadmap import runstate
from argus.roadmap.evidence import is_done, needs_decision
from argus.roadmap.server import select_server_phase

APPROVAL_TICKED = "- [x] Required approval is obtained"
APPROVAL_EMPTY = "- [ ] Required approval is obtained"


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    """A configured project root with a roadmap folder, restored after the test."""
    root = tmp_path / "project"
    (root / "roadmap").mkdir(parents=True)
    previous = conventions.configure(
        {
            "repo-root": root,
            "pipeline": None,
            "roadmap-dir": Path("roadmap"),
            "number-input": "phase_number",
            "file-input": "phase_file",
            "driver.runs": Path("runs"),
            "driver.include": ["phase-*.md"],
            "machine.id": "machine-1",
        }
    )
    try:
        yield root
    finally:
        conventions.configure(previous)


def phase_file(repo: Path, number: int, *, approved: bool = False) -> Path:
    """The phase file for one number, with the approval box as asked."""
    path = repo / "roadmap" / f"phase-{number:06d}-first.md"
    box = APPROVAL_TICKED if approved else APPROVAL_EMPTY
    path.write_text(f"# Phase {number}\n\n{box}\n", encoding="utf-8")
    return path


def run_dir(repo: Path, number: int) -> Path:
    """The run directory for one number, created."""
    directory = repo / "runs" / f"phase-{number:06d}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_blocked_run_with_the_box_ticked_is_not_done(repo: Path) -> None:
    number = 100032
    path = phase_file(repo, number, approved=True)
    write_json(run_dir(repo, number) / "run.json", {"state": "blocked"})
    assert is_done(repo, number, path, set()) is False


def test_blocked_run_without_the_box_is_not_done(repo: Path) -> None:
    number = 100032
    path = phase_file(repo, number)
    write_json(run_dir(repo, number) / "run.json", {"state": "blocked"})
    assert is_done(repo, number, path, set()) is False


def test_interrupted_run_with_no_run_json_is_not_done(repo: Path) -> None:
    # The 100913 class: the run directory and its resume state survive, but the
    # run record does not. There is nothing else to read, so the box must not
    # close the phase out.
    number = 100913
    path = phase_file(repo, number, approved=True)
    write_json(run_dir(repo, number) / "resume.json", {"current": "publish"})
    assert is_done(repo, number, path, set()) is False


def test_completed_run_is_done(repo: Path) -> None:
    number = 100040
    path = phase_file(repo, number)
    write_json(run_dir(repo, number) / "run.json", {"state": "completed"})
    assert is_done(repo, number, path, set()) is True


def test_publication_receipt_is_done(repo: Path) -> None:
    number = 100041
    path = phase_file(repo, number)
    write_json(
        run_dir(repo, number) / "publication.json",
        {"version": 1, "state": "published", "phase": f"{number:06d}"},
    )
    assert is_done(repo, number, path, set()) is True


def test_ticked_box_still_closes_out_a_finished_run(repo: Path) -> None:
    # The hand close-out is narrowed, not removed: a run that ended in a terminal
    # state and left no other marker still counts once the box is ticked.
    number = 100042
    path = phase_file(repo, number, approved=True)
    write_json(run_dir(repo, number) / "run.json", {"state": "rejected"})
    assert is_done(repo, number, path, set()) is True


def test_abandoned_phase_is_reported_rather_than_offered(repo: Path) -> None:
    number = 100050
    phase_file(repo, number)
    write_json(
        run_dir(repo, number) / "abandoned.json",
        {"version": 1, "phase": f"{number:06d}"},
    )
    assert needs_decision(repo, number) == "abandoned"


def test_an_invalid_abandoned_residue_does_not_park_the_phase(repo: Path) -> None:
    # The residue is validated, not merely found: a torn file, or one that names
    # another phase, must not take this phase out of the queue for ever.
    for number, residue in (
        (100051, {}),
        (100052, {"phase": "100053"}),
        (100053, {"version": 2, "phase": "100053"}),
    ):
        write_json(run_dir(repo, number) / "abandoned.json", residue)
        assert runstate.run_abandoned(repo, number) is False
        assert needs_decision(repo, number) is None


def test_needs_decision_states_are_unchanged(repo: Path) -> None:
    write_json(run_dir(repo, 100060) / "run.json", {"state": "rejected"})
    write_json(run_dir(repo, 100061) / "run.json", {"state": "remedy_escalated"})
    write_json(run_dir(repo, 100062) / "run.json", {"state": "blocked"})
    assert needs_decision(repo, 100060) == "rejected"
    assert needs_decision(repo, 100061) == "remedy_escalated"
    assert needs_decision(repo, 100062) is None
    assert needs_decision(repo, 100063) is None


def test_run_unfinished_answers_for_both_resume_classes(repo: Path) -> None:
    write_json(run_dir(repo, 100070) / "run.json", {"state": "blocked"})
    write_json(run_dir(repo, 100071) / "resume.json", {"current": "verify"})
    write_json(run_dir(repo, 100072) / "run.json", {"state": "completed"})
    assert runstate.run_unfinished(repo, 100070) is True
    assert runstate.run_unfinished(repo, 100071) is True
    assert runstate.run_unfinished(repo, 100072) is False
    assert runstate.run_unfinished(repo, 100073) is False
    assert runstate.run_state(repo, 100070) == "blocked"
    assert runstate.run_state(repo, 100071) is None
    assert runstate.run_abandoned(repo, 100071) is False


class FakeStore:
    """A board double that answers reads from memory and never reserves."""

    def __init__(self, phases: dict[str, dict[str, Any]] | None = None) -> None:
        self.state: dict[str, Any] = {"version": 1, "phases": phases or {}}

    def read(self) -> tuple[dict[str, Any], str]:
        return self.state, "fake"


GATE_OPEN = (
    "### Decision Gates\n"
    "| Gate | Issue | State | Choice | Untaken branch |\n"
    "|---|---|---|---|---|\n"
    "| Which option ships | #1 | `open` | — | — |\n"
)


def gated_phase_file(repo: Path, number: int) -> Path:
    """A phase file whose plan carries an open decision gate."""
    path = repo / "roadmap" / f"phase-{number:06d}-first.md"
    path.write_text(GATE_OPEN, encoding="utf-8")
    return path


def abandon(repo: Path, number: int) -> None:
    """Park one phase with its abandoned residue."""
    write_json(
        run_dir(repo, number) / "abandoned.json",
        {"version": 1, "phase": f"{number:06d}"},
    )


def select(repo: Path, store: FakeStore) -> dict[str, Any]:
    """Run the selector read-only against a board double."""
    return select_server_phase(
        repo, "server-01", store=store, reserve=False, roadmap=repo / "roadmap"
    )


def decision_states(got: dict[str, Any]) -> list[str]:
    entries: list[dict[str, Any]] = got.get("needs_decision") or []
    return [entry["state"] for entry in entries]


def test_abandoned_phase_with_an_open_gate_is_reported(repo: Path) -> None:
    # The parked report must not be swallowed by the open-gate guard, and the
    # open gate must not stop the line either: the operator who parked the phase
    # has already decided, so the selector reports the residue and moves on.
    number = 100080
    gated_phase_file(repo, number)
    abandon(repo, number)
    got = select(repo, FakeStore())
    assert got["state"] == "NO_PHASE", got
    assert got["needs_decision"] == [
        {
            "phase": f"{number:06d}",
            "path": f"roadmap/phase-{number:06d}-first.md",
            "state": "abandoned",
        }
    ], got


def test_abandoned_phase_with_an_open_gate_and_a_board_entry(repo: Path) -> None:
    number = 100081
    gated_phase_file(repo, number)
    abandon(repo, number)
    store = FakeStore({f"{number:06d}": {"status": "claimed"}})
    got = select(repo, store)
    assert got["state"] == "NO_PHASE", got
    assert decision_states(got) == ["abandoned"], got


def test_completed_abandoned_run_is_skipped_but_reported(repo: Path) -> None:
    # A run that finished and was then parked is still skipped -- its work is
    # done -- but the residue must not vanish from the report.
    number = 100082
    phase_file(repo, number)
    write_json(run_dir(repo, number) / "run.json", {"state": "completed"})
    abandon(repo, number)
    got = select(repo, FakeStore())
    assert got["state"] == "NO_PHASE", got
    assert decision_states(got) == ["abandoned"], got


def test_completed_abandoned_run_with_a_board_entry_is_skipped_but_reported(
    repo: Path,
) -> None:
    number = 100083
    phase_file(repo, number)
    write_json(run_dir(repo, number) / "run.json", {"state": "completed"})
    abandon(repo, number)
    store = FakeStore({f"{number:06d}": {"status": "claimed"}})
    got = select(repo, store)
    assert got["state"] == "NO_PHASE", got
    assert decision_states(got) == ["abandoned"], got
