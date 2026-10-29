# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The verdict that holds an issue whose run completed with no pull request."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.completed_run import pull_request_verdict
from argus.issues.transport import IssuesUnavailable
from issues_fixture import fake_gh, fixture_update, issue, settings

TASK = "issue-100001-fix-the-input.md"
URL = "https://github.com/example/project/pull/70"


@pytest.fixture
def repo(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    yield tmp_path
    conventions.configure(previous)


@pytest.mark.usefixtures("repo")
def test_no_argus_pull_request_gives_none() -> None:
    # Given no pull request on the issue branch, when the verdict is read, then it
    # is none, so the driver may raise Needs You.
    assert pull_request_verdict(100001, TASK) == "none"


@pytest.mark.parametrize("state", ["OPEN", "CLOSED", "MERGED"])
def test_an_argus_pull_request_in_any_state_gives_pull_request(
    repo: Path, state: str
) -> None:
    # Given an Argus pull request in any state, when the verdict is read, then it
    # is pull_request, so the issue stays owned by that pull request.
    row: dict[str, Any] = {
        "number": 70,
        "url": URL,
        "state": state,
        "headRefName": "issue/100001-fix-the-input",
        "isCrossRepository": False,
    }
    fixture_update(repo, branch_pulls=[row])
    assert pull_request_verdict(100001, TASK) == "pull_request"


def test_an_unreadable_branch_list_raises(repo: Path) -> None:
    # Given the pull request list cannot be read, when the verdict is read, then it
    # raises, so the driver starts no run and says nothing.
    fixture_update(repo, pr_list="failure")
    with pytest.raises(IssuesUnavailable):
        pull_request_verdict(100001, TASK)
