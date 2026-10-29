# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""An open Argus pull request owns its issue; a ready one is never repaired."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.gitsync import repair_cli
from argus.issues.source import IssuesSource
from argus.issues.transport import IssuesUnavailable
from issues_fixture import Board, fake_gh, fixture_update, issue, settings


def pull(branch: str, *, draft: bool = True, fork: bool = False) -> dict[str, Any]:
    owner = "fork" if fork else "example"
    head = {"ref": branch, "repo": {"full_name": f"{owner}/project"}}
    return {"head": head, "draft": draft}


@pytest.fixture
def repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Path]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    board = Board()

    def store(*_a: object, **_k: object) -> Board:
        return board

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    yield tmp_path
    conventions.configure(previous)


def test_an_issue_with_no_pull_request_is_eligible(repo: Path) -> None:
    fixture_update(repo, pulls=[pull("issue/100009-other"), pull("feature/x")])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "AVAILABLE" and result["issue"] == "100001"


@pytest.mark.parametrize("draft", [True, False])
def test_an_open_argus_pull_request_holds_its_issue(repo: Path, draft: bool) -> None:
    fixture_update(repo, pulls=[pull("issue/100001-fix-input", draft=draft)])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "NO_PHASE"
    assert result["skipped"]["open_pull_request"] == 1


@pytest.mark.parametrize(
    "row",
    [
        pull("issue/100001-fix-input", fork=True),
        pull("issue/100001-fix-input\n; rm -rf /"),
        pull("x/issue/100001-fix-input"),
        {"head": {"ref": ["issue/100001-x"], "repo": {"full_name": "example/project"}}},
        {"head": {"ref": "issue/100001-x", "repo": None}},
    ],
)
def test_a_branch_that_is_not_argus_own_does_not_hold(
    repo: Path, row: dict[str, Any]
) -> None:
    fixture_update(repo, pulls=[row])
    assert IssuesSource(repo).select(reserve=False)["issue"] == "100001"


@pytest.mark.parametrize(
    "mode", [{"mode": "failure", "exit": 1}, {"mode": "invalid"}, {"mode": "empty"}]
)
def test_an_unread_pull_request_list_selects_nothing(
    repo: Path, mode: dict[str, Any]
) -> None:
    # Given the issue search succeeds, when the pull request list cannot be read,
    # then selection raises and no issue is chosen.
    source = IssuesSource(repo)

    def unread() -> set[int]:
        fixture_update(repo, **mode)
        return source.client.argus_pull_requests()

    source.evidence.owned = unread
    with pytest.raises(IssuesUnavailable):
        source.select(reserve=False)


def test_a_pull_request_on_a_later_page_still_holds(repo: Path) -> None:
    # Given more open pull requests than one page, when the Argus one is on the
    # second page, then its issue is still held.
    first = [pull(f"feature/{n}") for n in range(100)]
    second = [pull("issue/100001-fix-input")]
    fixture_update(repo, pull_pages=[first, second])
    result = IssuesSource(repo).select(reserve=False)
    assert result["skipped"]["open_pull_request"] == 1


def test_a_ready_pull_request_is_never_listed_for_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "runs" / "issue-7").mkdir(parents=True)
    ready = {"number": 70, "url": "https://g/o/r/pull/70", "state": "OPEN"}

    def listed(_root: Path, branch: str) -> tuple[list[dict[str, Any]], str]:
        row = {"headRefName": branch, "isCrossRepository": False, "isDraft": False}
        return [{**ready, **row}], ""

    def scope(_root: object, _number: int) -> tuple[Path, list[str]]:
        return tmp_path, ["runs/issue-7", "tasks/issue-7-fix-ci.md"]

    def runs_dir(_root: object) -> Path:
        return tmp_path / "runs"

    monkeypatch.setattr(repair_cli.conventions, "runs_dir", runs_dir)
    monkeypatch.setattr(repair_cli, "issue_scope", scope)
    monkeypatch.setattr(repair_cli, "gh_pull_requests_for_branch", listed)
    assert repair_cli.issue_pull_requests(tmp_path) == []
