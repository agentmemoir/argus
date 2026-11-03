# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A failed issue listing: three spaced attempts, one Needs You, then a clear reset."""

from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import repair_cli
from argus.issues import evidence

WAIT = 10 * 60  # the spacing that PHILOSOPHY.md section 3 names: 10 minutes
FAIL = (None, "gh ran the list and failed (gh exited 1)")
OK: tuple[list[dict[str, Any]], str] = ([], "")


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    runs = tmp_path / "runs"
    (runs / "issue-7").mkdir(parents=True)
    state: dict[str, Any] = {"gh": FAIL, "calls": 0, "root": tmp_path}

    def gh(_root: Path, _branch: str) -> tuple[Any, str]:
        state["calls"] += 1
        return state["gh"]

    monkeypatch.setattr(repair_cli.conventions, "runs_dir", lambda _root: runs)
    monkeypatch.setattr(repair_cli, "issue_scope", lambda _r, _n: (None, (None, "t")))
    monkeypatch.setattr(repair_cli, "issue_branch", lambda _n, _t: "issue/7-x")
    monkeypatch.setattr(repair_cli, "gh_pull_requests_for_branch", gh)
    return state


def _tick(listing: dict[str, Any], now: float) -> tuple[list[int], list[str]]:
    unread: list[int] = []
    notices: list[str] = []
    repair_cli.issue_pull_requests(listing["root"], unread, notices, now=now)
    return unread, notices


def test_a_failed_read_holds_the_tick_inside_its_attempts(
    listing: dict[str, Any],
) -> None:
    unread, notices = _tick(listing, 0.0)
    assert unread == [7]
    assert notices == []


def test_no_read_is_made_before_the_attempt_is_due(listing: dict[str, Any]) -> None:
    _tick(listing, 0.0)
    unread, notices = _tick(listing, 300.0)
    assert listing["calls"] == 1
    assert unread == [7]
    assert notices == []


def test_the_third_failure_raises_one_notice_and_leaves_the_issue_out(
    listing: dict[str, Any],
) -> None:
    notices: list[str] = []
    unread: list[int] = []
    for now in (0.0, WAIT, 2 * WAIT):
        unread, notices = [], []
        repair_cli.issue_pull_requests(listing["root"], unread, notices, now=now)
    assert unread == []
    assert len(notices) == 1
    assert "issue 7" in notices[0] and "3 attempts" in notices[0]
    assert "gh ran the list and failed (gh exited 1)" in notices[0]
    assert evidence.left_out(listing["root"], 7, None)
    later, more = _tick(listing, 3 * WAIT)
    assert later == [] and more == []
    assert listing["calls"] == 4


def test_a_success_clears_the_count_and_the_exclusion(listing: dict[str, Any]) -> None:
    for now in (0.0, WAIT, 2 * WAIT):
        _tick(listing, now)
    assert evidence.left_out(listing["root"], 7, None)
    listing["gh"] = OK
    unread, _ = _tick(listing, 3 * WAIT)
    assert unread == []
    assert not evidence.left_out(listing["root"], 7, None)
    from argus.gitsync import issue_read_fault

    assert not issue_read_fault.record_path(listing["root"], 7).exists()
    listing["gh"] = FAIL
    unread, notices = _tick(listing, 4 * WAIT)
    assert unread == [7]
    assert notices == []
