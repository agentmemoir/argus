# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A closed Argus pull request does not block a new claim: it gets a new branch."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.client import IssueClient
from argus.issues.evidence import Evidence
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings

ELIGIBLE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
BASE = "issue/100001-fix-the-input"
URL = "https://github.com/example/project/pull/70"


def held() -> dict[str, Any]:
    return {
        "issue": "100001",
        "machine_id": "worker-one",
        "status": "completed",
        "reservation_id": "rsv-own",
        "generation": 1,
        "run_id": "issue-100001",
    }


def history_row(state: str, branch: str, number: int = 70) -> dict[str, Any]:
    """A pull request as the all-state listing returns it."""
    return {
        "number": number,
        "html_url": f"https://github.com/example/project/pull/{number}",
        "state": "open" if state == "open" else "closed",
        "merged_at": "2026-10-29T12:00:00Z" if state == "merged" else None,
        "draft": False,
        "head": {
            "ref": branch,
            "repo": {"full_name": "example/project"},
            "sha": "a" * 40,
        },
    }


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Board:
    shared = Board()

    def store(*_a: object, **_k: object) -> Board:
        return shared

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    return shared


@pytest.fixture
def repo(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    binary = fake_gh(
        tmp_path, [issue(labels=ELIGIBLE)], add_cleanup=request.addfinalizer
    )
    previous = conventions.override(settings(tmp_path, binary))
    yield tmp_path
    conventions.configure(previous)


def test_a_closed_pull_request_and_no_label_start_a_fresh_branch(
    repo: Path, board: Board
) -> None:
    # Given the claim label is removed, the published record is held here, and the
    # Argus pull request closed without merge, when the issue is selected, then the
    # published record is retired and the new claim uses issue/<n>-<slug>-2.
    board.records["100001"] = held()
    fixture_update(repo, history=[history_row("closed", BASE)])
    result = IssuesSource(repo).select()
    assert result["state"] == "RESERVED"
    assert Path(result["path"]).name == "issue-100001-fix-the-input-2.md"
    assert board.retired == ["100001"]


def test_the_next_fresh_branch_skips_every_suffix_already_used(
    repo: Path, board: Board
) -> None:
    # Given closed pull requests on the base branch and on suffix -2, when the issue
    # is selected, then the new claim uses suffix -3.
    board.records["100001"] = held()
    fixture_update(
        repo,
        history=[
            history_row("closed", BASE, 70),
            history_row("closed", f"{BASE}-2", 71),
        ],
    )
    result = IssuesSource(repo).select()
    assert Path(result["path"]).name == "issue-100001-fix-the-input-3.md"


@pytest.mark.usefixtures("board")
def test_a_merged_pull_request_on_a_suffixed_branch_keeps_the_issue_out(
    repo: Path,
) -> None:
    # Given a merged pull request on the suffixed branch, when the issue is checked,
    # then the issue is never selected, whatever its branch suffix.
    fixture_update(repo, history=[history_row("merged", f"{BASE}-2", 71)])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "NO_PHASE"
    assert result["skipped"]["merged_pull_request"] == 1


@pytest.mark.usefixtures("board")
def test_an_open_pull_request_on_a_suffixed_branch_holds_the_issue(
    repo: Path,
) -> None:
    # Given an open pull request on the suffixed branch, when the issue is checked,
    # then it is held as an open Argus pull request.
    fixture_update(
        repo,
        pulls=[
            {
                "number": 71,
                "html_url": "https://github.com/example/project/pull/71",
                "draft": True,
                "head": {
                    "ref": f"{BASE}-2",
                    "repo": {"full_name": "example/project"},
                    "sha": "a" * 40,
                },
            }
        ],
    )
    result = IssuesSource(repo).select(reserve=False)
    assert result["skipped"]["open_pull_request"] == 1


def test_a_closed_suffixed_pull_request_is_noticed_as_the_same_issue(
    repo: Path, board: Board
) -> None:
    # Given the label still holds the issue and its suffixed pull request closed
    # without merge, when a tick runs, then one Needs You notice names that pull request.
    board.records["100001"] = held()
    fixture_update(
        repo,
        rows=[issue(labels=[*ELIGIBLE, {"name": "accepted"}])],
        history=[history_row("closed", f"{BASE}-2", 71)],
    )
    source = IssuesSource(repo)
    source.select()
    assert [notice["key"] for notice in source.notices] == ["closed-100001"]


@pytest.mark.usefixtures("board")
def test_closed_detection_reads_the_suffixed_branch(repo: Path) -> None:
    # Given a closed pull request on the suffixed branch, when the pull request history
    # is read, then the issue is reported as closed without merge.
    fixture_update(repo, history=[history_row("closed", f"{BASE}-2", 71)])
    evidence = Evidence(IssueClient())
    assert evidence.closed_issues() == {100001}
    assert evidence.merged_issues() == set()
