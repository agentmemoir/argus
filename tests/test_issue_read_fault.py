# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A failed issue listing: three spaced attempts, one Needs You, then a clear reset."""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.gitsync import issue_read_fault, repair_cli
from argus.issues import evidence

WAIT = 10 * 60  # the spacing that PHILOSOPHY.md section 3 names: 10 minutes
FAIL: tuple[None, str] = (None, "gh ran the list and failed (gh exited 1)")
OK: tuple[list[dict[str, Any]], str] = ([], "")


def _scope(_root: object, _number: int) -> tuple[None, tuple[None, str]]:
    return None, (None, "t")


def _branch(_number: int, _task: object) -> str:
    return "issue/7-x"


@pytest.fixture
def listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[dict[str, Any]]:
    runs = tmp_path / "runs"
    (runs / "issue-000007").mkdir(parents=True)
    state: dict[str, Any] = {"gh": FAIL, "calls": 0, "root": tmp_path, "runs": runs}

    def gh(_root: Path, _branch: str) -> tuple[Any, str]:
        state["calls"] += 1
        return state["gh"]

    def runs_dir(_root: object) -> Path:
        return runs

    previous = conventions.override({"driver.state": "state"})
    monkeypatch.setattr(repair_cli.conventions, "runs_dir", runs_dir)
    monkeypatch.setattr(repair_cli, "issue_scope", _scope)
    monkeypatch.setattr(repair_cli, "issue_branch", _branch)
    monkeypatch.setattr(repair_cli, "gh_pull_requests_for_branch", gh)
    yield state
    conventions.configure(previous)


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
    assert not issue_read_fault.record_path(listing["root"], 7).exists()
    listing["gh"] = FAIL
    unread, notices = _tick(listing, 4 * WAIT)
    assert unread == [7]
    assert notices == []


def test_wrong_value_types_in_the_count_file_count_as_no_record(
    listing: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    # Valid JSON with the wrong types used to raise outside the read, so every tick
    # stopped. It now counts as no record, and the damage is reported once.
    path = issue_read_fault.record_path(listing["root"], 7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attempts": "soon"}), encoding="utf-8")
    unread, _ = _tick(listing, 0.0)
    assert unread == [7]
    assert "not valid" in capsys.readouterr().err
    _tick(listing, 300.0)
    assert "not valid" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        '{"attempts": [NaN]}',
        '{"attempts": [Infinity]}',
        '{"attempts": [' + "1" + "0" * 400 + "]}",
    ],
    ids=["nan", "infinity", "integer-too-large-for-a-float"],
)
def test_a_count_that_is_not_a_finite_time_counts_as_no_record(
    listing: dict[str, Any], capsys: pytest.CaptureFixture[str], text: str
) -> None:
    # NaN and Infinity never come due, and a huge integer raised out of the tick.
    # Each one counts as no record, so the tick reads the pull request again.
    path = issue_read_fault.record_path(listing["root"], 7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    unread, notices = _tick(listing, 0.0)
    assert listing["calls"] == 1
    assert unread == [7]
    assert notices == []
    assert "not valid" in capsys.readouterr().err


def test_the_leftover_sweep_does_not_stage_the_count(
    listing: dict[str, Any], tmp_path: Path
) -> None:
    # The sweep stages the issue's scope with git add -A -- <run folder>
    # (gitsync/issue_runstate.py). The count sits in the driver state folder, which is
    # outside that scope, so the sweep never stages it.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    run_folder = listing["runs"] / "issue-000007"
    (run_folder / "task.md").write_text("# fix\n", encoding="utf-8")
    _tick(listing, 0.0)
    assert issue_read_fault.record_path(listing["root"], 7).exists()
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "--literal-pathspecs",
            "add",
            "-A",
            "--",
            "runs/issue-000007",
        ],
        check=True,
    )
    staged = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert staged == ["runs/issue-000007/task.md"]
