# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The issue pull requests Argus owns: board records, and labels after a merge."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationError
from argus.issues.source import IssuesSource
from argus.issues.transport import IssuesUnavailable
from issues_fixture import Board, argv, fake_gh, fixture_update, issue, settings

LABEL = "accepted"
ELIGIBLE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
BRANCH = "issue/100001-fix-the-input"
PULL_URL = "https://github.com/example/project/pull/70"


def held(*, machine: str = "worker-one", status: str = "running") -> dict[str, Any]:
    return {
        "issue": "100001",
        "machine_id": machine,
        "status": status,
        "reservation_id": "rsv-own",
        "generation": 1,
        "run_id": "issue-100001",
    }


def merged_row(state: str = "MERGED") -> dict[str, Any]:
    return {
        "number": 70,
        "url": PULL_URL,
        "state": state,
        "headRefName": BRANCH,
        "isCrossRepository": False,
    }


def open_row() -> dict[str, Any]:
    return {
        "number": 70,
        "html_url": PULL_URL,
        "draft": True,
        "head": {
            "ref": BRANCH,
            "repo": {"full_name": "example/project"},
            "sha": "a" * 40,
        },
    }


def removals(repo: Path) -> list[list[str]]:
    return [
        call["argv"]
        for call in argv(repo)
        if call["argv"][:2] == ["issue", "edit"] and "--remove-label" in call["argv"]
    ]


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Board:
    shared = Board()

    def store(*_a: object, **_k: object) -> Board:
        return shared

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    return shared


@pytest.fixture
def repo(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    rows = [issue(labels=[*ELIGIBLE, {"name": LABEL}])]
    binary = fake_gh(tmp_path, rows, add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    yield tmp_path
    conventions.configure(previous)


def test_a_merged_pull_request_removes_the_label_of_a_published_issue(
    repo: Path, board: Board
) -> None:
    # Given a published issue with the claim label and a merged Argus pull request,
    # when the tick runs, then the label is removed and the published record stays.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, branch_pulls=[merged_row()])
    IssuesSource(repo).select()
    assert len(removals(repo)) == 1
    assert board.released == []
    assert board.records["100001"]["status"] == "completed"


def test_a_second_tick_after_the_merge_removes_nothing(
    repo: Path, board: Board
) -> None:
    # Given the first tick removed the label, when a second tick runs, then no
    # further label removal is sent.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, branch_pulls=[merged_row()])
    IssuesSource(repo).select()
    fixture_update(repo, rows=[issue(labels=ELIGIBLE)])
    IssuesSource(repo).select()
    assert len(removals(repo)) == 1


def test_a_failed_label_removal_is_retried_on_the_next_tick(
    repo: Path, board: Board
) -> None:
    # Given a published issue, a merged pull request, and a label write that fails,
    # when the tick runs, then the tick returns and the record is unchanged; when the
    # next tick's write works, then the label is removed.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, branch_pulls=[merged_row()], mode="edit-failure")
    IssuesSource(repo).select()
    assert board.records["100001"]["status"] == "completed"
    fixture_update(repo, mode="working")
    IssuesSource(repo).select()
    assert len(removals(repo)) == 2


def test_an_unreadable_merge_check_changes_nothing(repo: Path, board: Board) -> None:
    # Given a published issue and an unreadable pull request list, when the tick
    # runs, then selection raises and no label or board change is made.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, pr_list="failure")
    with pytest.raises(IssuesUnavailable):
        IssuesSource(repo).select()
    assert removals(repo) == []
    assert board.released == []


def test_an_active_claim_keeps_its_label_after_a_merge(
    repo: Path, board: Board
) -> None:
    # Given an active claim held here and a merged Argus pull request, when the tick
    # runs, then the claim and its label are left to the run.
    board.records["100001"] = held()
    fixture_update(repo, branch_pulls=[merged_row()])
    IssuesSource(repo).select()
    assert removals(repo) == []
    assert board.released == []


def test_an_open_pull_request_is_recorded_once_on_its_issue(
    repo: Path, board: Board
) -> None:
    # Given a published issue owned here and an open Argus pull request, when two
    # ticks run, then the board record names the branch and URL, written once.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, pulls=[open_row()])
    IssuesSource(repo).select()
    IssuesSource(repo).select()
    assert board.records["100001"]["pull_request"] == {
        "branch": BRANCH,
        "url": PULL_URL,
    }
    assert board.pull_writes == [{"branch": BRANCH, "url": PULL_URL}]


def test_another_machines_record_is_not_written(repo: Path, board: Board) -> None:
    # Given an open Argus pull request on a record owned by another machine, when the
    # tick runs, then this machine writes nothing to that record.
    board.records["100001"] = held(machine="worker-two", status="completed")
    fixture_update(repo, pulls=[open_row()])
    IssuesSource(repo).select()
    assert board.pull_writes == []


def test_a_refused_board_write_raises_nothing(
    repo: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the board refuses the pull request write, when the tick runs, then the
    # tick returns and the record has no pull request field.
    board.records["100001"] = held(status="completed")
    fixture_update(repo, pulls=[open_row()])

    def refuse(_reservation: dict[str, Any], _pull: dict[str, Any]) -> None:
        raise CoordinationError("board is busy")

    monkeypatch.setattr(board, "set_issue_pull_request", refuse)
    IssuesSource(repo).select()
    assert "pull_request" not in board.records["100001"]
