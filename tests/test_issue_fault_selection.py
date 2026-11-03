# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The real issue selector skips an issue whose listing attempts are spent."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from argus import conventions
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings

EASY, FREE = 100001, 100002
WAIT_S = 600


def _board(*_args: object, **_kwargs: object) -> Board:
    return Board()


@pytest.fixture
def repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Path]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    values = settings(tmp_path, binary)
    # The driver's own state folder, which the listing's count file is kept in.
    values["driver.state"] = "state"
    previous = conventions.override(values)
    monkeypatch.setattr("argus.issues.source.ReservationStore", _board)
    hard = issue(
        FREE, labels=[{"name": n} for n in ("bug", "area:store", "difficulty:hard")]
    )
    fixture_update(tmp_path, rows=[issue(EASY), hard])
    conventions.override({"issues.preference": "easiest"})
    yield tmp_path
    conventions.configure(previous)


def _three_failed_reads(repo: Path) -> None:
    # The count file the repair listing writes after its third failed attempt:
    # read-fault-issue-<number>.json in the driver's state folder.
    path = conventions.runs_dir(repo) / "state" / f"read-fault-issue-{EASY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    attempts = [0.0, float(WAIT_S), float(2 * WAIT_S)]
    path.write_text(json.dumps({"attempts": attempts}), encoding="utf-8")


def test_the_selector_does_not_select_an_issue_whose_attempts_are_spent(
    repo: Path,
) -> None:
    _three_failed_reads(repo)
    result = IssuesSource(repo).select(reserve=False)
    assert result["issue"] == str(FREE)
    assert result["state"] == "AVAILABLE"


def test_without_a_fault_the_same_selector_picks_the_first_issue(repo: Path) -> None:
    # The control: the same rows and preference select the issue the fault excludes.
    result = IssuesSource(repo).select(reserve=False)
    assert result["issue"] == str(EASY)
