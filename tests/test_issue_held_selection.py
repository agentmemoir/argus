# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A finished held claim does not stop the queue; a checked close still goes first.

This machine holds the claim on an open issue that comes first in the order. When
that run finished with no checked no-change file, selection skips the claim, the
queue goes on to later issues, and the tick raises the claim's one Needs You line.
When a checked no-change run waits for its close, selection still returns the wait,
so the driver closes the issue first. A claim whose run is running, or has no run
state, still blocks the queue, so a second run never starts. A refused reservation
also leaves the frozen task snapshot of the held claim unchanged.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.reservations import ReservationCooldown
from argus.issues import claims
from argus.issues.completed_run import NO_CHANGE_FILE
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings
from no_change_fixture import NO_REASON_NOTICE, Case
from no_change_fixture import case as case  # noqa: F401

ELIGIBLE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
HELD = [*ELIGIBLE, {"name": "accepted"}]
TASK = "tasks/issue-100001-fix-the-input.md"


class CoolingBoard(Board):
    """A board whose expired claim is still in its cooldown window."""

    def reserve(
        self,
        number: int,
        machine: str,
        *,
        run_id: str,  # noqa: ARG002 - always refused
    ) -> dict[str, Any]:
        raise ReservationCooldown(number, 30, {"machine_id": machine})


def own_claim(status: str = "claimed") -> dict[str, Any]:
    return {
        "issue": "100001",
        "machine_id": "worker-one",
        "status": status,
        "reservation_id": "rsv-own",
        "generation": 1,
        "run_id": "issue-100001",
    }


@pytest.fixture
def repo(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    binary = fake_gh(
        tmp_path, [issue(labels=ELIGIBLE)], add_cleanup=request.addfinalizer
    )
    previous = conventions.override(settings(tmp_path, binary))
    yield tmp_path
    conventions.configure(previous)


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Board:
    shared = Board()

    def store(*_a: object, **_k: object) -> Board:
        return shared

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    return shared


def write_task(repo: Path, text: str) -> Path:
    path = repo / TASK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def hold_own_claim(repo: Path, board: Board, run_state: str | None) -> None:
    """This machine holds issue 100001 with a valid frozen snapshot."""
    claims.save(repo, 100001, own_claim(), write_task(repo, "frozen task\n"))
    board.records["100001"] = own_claim()
    if run_state is not None:
        folder = repo / "runs" / "issue-100001"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run.json").write_text(
            json.dumps({"state": run_state}), encoding="utf-8"
        )


def test_a_held_issue_with_a_finished_run_does_not_stop_the_queue(
    repo: Path, board: Board
) -> None:
    # Given this machine holds the first issue with its label, its run completed with
    # no pull request and no checked close, and the second issue is free. When
    # selection runs, then the second issue is reserved.
    hold_own_claim(repo, board, "completed")
    fixture_update(
        repo,
        rows=[issue(100001, labels=HELD), issue(100002, labels=ELIGIBLE)],
    )
    result = IssuesSource(repo).select()
    assert result["state"] == "RESERVED" and result["issue"] == "100002", result
    assert board.claims == [100002]


def test_a_finished_held_claim_raises_one_needs_you_line_on_each_tick(
    repo: Path, board: Board
) -> None:
    # Given the same finished claim and a free second issue. When two ticks run, then
    # each tick carries one Needs You line for the first issue, with the same text.
    hold_own_claim(repo, board, "completed")
    fixture_update(
        repo,
        rows=[issue(100001, labels=HELD), issue(100002, labels=ELIGIBLE)],
    )
    first = IssuesSource(repo)
    assert first.select()["state"] == "RESERVED"
    second = IssuesSource(repo)
    second.select()
    assert [line["key"] for line in first.notices] == ["completed-100001"]
    assert first.notices == second.notices
    assert first.notices[0]["text"].endswith("Action: remove the claim label to retry.")


def test_a_checked_finished_claim_still_waits_so_the_driver_closes_it_first(
    repo: Path, board: Board
) -> None:
    # Given the first issue is this machine's finished claim, its run folder holds a
    # checked no-change file, and a second issue is free. When selection runs, then it
    # returns the wait with the frozen path, reserves nothing, and raises no line here.
    hold_own_claim(repo, board, "completed")
    (repo / "runs" / "issue-100001" / NO_CHANGE_FILE).write_text(
        "checked\n", encoding="utf-8"
    )
    fixture_update(
        repo,
        rows=[issue(100001, labels=HELD), issue(100002, labels=ELIGIBLE)],
    )
    source = IssuesSource(repo)
    result = source.select()
    assert result["state"] == "WAIT_FOR_CLAIM" and result["issue"] == "100001", result
    assert result["path"] == TASK and board.claims == []
    assert source.notices == []


def test_the_driver_raises_one_needs_you_line_across_two_ticks(case: Case) -> None:
    # Given a finished claim with no checked no-change file, first in the queue. When
    # two ticks run, then the one Needs You line is shown once and nothing closes.
    (case.run_folder() / NO_CHANGE_FILE).unlink()
    case.tick()
    case.tick()
    assert case.notes() == [NO_REASON_NOTICE]
    assert case.closes() == []


@pytest.mark.parametrize("run_state", ["running", None])
def test_a_live_claim_on_this_machine_still_blocks_a_second_launch(
    repo: Path, board: Board, run_state: str | None
) -> None:
    # Given this machine holds the first issue and its run is not finished, when
    # selection runs, then it waits on that claim and reserves nothing else.
    hold_own_claim(repo, board, run_state)
    fixture_update(
        repo,
        rows=[issue(100001, labels=ELIGIBLE), issue(100002, labels=ELIGIBLE)],
    )
    result = IssuesSource(repo).select()
    assert result["state"] == "WAIT_FOR_CLAIM" and result["issue"] == "100001", result
    assert board.claims == []


def test_a_refused_reservation_leaves_the_frozen_snapshot_valid(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an expired claim in its cooldown, with its frozen snapshot on disk, and
    # a changed issue body, when selection tries to claim the issue and the board
    # refuses, then the frozen snapshot still matches its file.
    cooling = CoolingBoard()

    def store(*_a: object, **_k: object) -> CoolingBoard:
        return cooling

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    task = write_task(repo, "old frozen\n")
    claims.save(repo, 100001, own_claim("expired"), task)
    cooling.records["100001"] = own_claim("expired")
    fixture_update(repo, rows=[issue(labels=ELIGIBLE, body="a changed body")])
    assert claims.snapshot(repo, 100001, own_claim("expired")) is not None
    result = IssuesSource(repo).select()
    assert result["state"] == "COOLDOWN", result
    assert task.read_text(encoding="utf-8") == "old frozen\n"
    assert claims.snapshot(repo, 100001, own_claim("expired")) is not None


def test_a_failed_snapshot_write_releases_the_new_claim(
    repo: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the board grants the claim but the task snapshot cannot be written, when
    # selection runs, then the error is raised and the new claim is released, so no
    # claim is left without its snapshot.
    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("argus.issues.source.write", fail)
    fixture_update(repo, rows=[issue(100001, labels=ELIGIBLE)])
    with pytest.raises(OSError, match="disk full"):
        IssuesSource(repo).select()
    assert board.released == ["100001"] and board.records == {}
