# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The ready sweep reads only the open draft pull requests GitHub lists.

GitHub is replaced at its call sites, so the test runs the real sweep and the
real open-list gate. The counters are the gh calls the sweep makes. The timeline
read is the one that grows with history, so it is the call the tests guard.
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import argus.gitsync.group_review as group_review
from argus import conventions
from argus.gitsync import gh_open, modes

PHASE = 100501
SIBLING = 100500
PULL_REQUEST = "https://github.com/example-org/example-repo/pull/7"
GREEN = [{"bucket": "pass", "state": "SUCCESS", "name": "ci", "link": ""}]
DRAFT: dict[str, Any] = {"state": "OPEN", "is_draft": True, "events": []}


class GitHub:
    """The gh calls the sweep can make, counted, over one open pull request list."""

    def __init__(self, drafts: dict[int, bool] | None, why: str = "") -> None:
        self.drafts = drafts
        self.why = why
        self.listed = 0
        self.timeline_reads = 0
        self.checked = 0
        self.marked = 0

    def listing(self, *_args: Any) -> tuple[dict[int, bool] | None, str]:
        self.listed += 1
        return self.drafts, self.why

    def history(self, *_args: Any) -> tuple[dict[str, Any], str]:
        self.timeline_reads += 1
        return DRAFT, ""

    def checks(self, *_args: Any, **_kwargs: Any) -> tuple[Any, int, str]:
        self.checked += 1
        return GREEN, 0, ""

    def mark(self, *_args: Any) -> tuple[bool, str]:
        self.marked += 1
        return True, ""


def _receipt(phase: int, group: list[int]) -> dict[str, Any]:
    """One published member's receipt, naming the group's shared pull request."""
    return {
        "phase": f"{phase:06d}",
        "state": "published",
        "branch": "feature/shared",
        "group": group,
        "pull_request": PULL_REQUEST,
    }


@pytest.fixture
def sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[..., dict[str, Any]]:
    """Run one ready sweep over the given receipts and issue rows."""

    def run(
        github: GitHub,
        receipts: dict[int, dict[str, Any]] | None = None,
        issue_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        found = receipts or {}

        def receipt_of(_root: Path, number: int) -> dict[str, Any] | None:
            return found.get(number)

        def entries_of(_root: Path) -> list[tuple[int, dict[str, Any]]]:
            return [(number, {}) for number in found]

        def rows_of(_root: Path) -> list[dict[str, Any]]:
            return list(issue_rows or [])

        monkeypatch.setattr(
            group_review, "gh_open_pull_request_numbers", github.listing
        )
        monkeypatch.setattr(
            group_review, "gh_pull_request_stage_history", github.history
        )
        monkeypatch.setattr(group_review, "gh_pull_request_checks", github.checks)
        monkeypatch.setattr(group_review, "gh_mark_pull_request_ready", github.mark)
        monkeypatch.setattr(group_review, "phase_receipt", receipt_of)
        monkeypatch.setattr(modes, "phase_receipt", receipt_of)
        monkeypatch.setattr(modes, "_phase_run_entries", entries_of)
        monkeypatch.setattr(modes, "issue_pull_requests", rows_of)
        run_dir = tmp_path / "run"
        run_dir.mkdir(exist_ok=True)
        return modes.mode_ready(tmp_path, run_dir)

    return run


def test_no_candidate_makes_no_open_list_call(sweep: Callable[..., Any]) -> None:
    github = GitHub(drafts={})
    out = sweep(github)
    assert out == {"ok": True, "mode": "ready", "state": "IDLE", "results": []}
    assert github.listed == 0


def test_merged_or_closed_pull_request_is_never_read(sweep: Callable[..., Any]) -> None:
    # A merged or closed pull request is absent from the open list.
    github = GitHub(drafts={})
    out = sweep(github, {PHASE: _receipt(PHASE, [PHASE])})
    assert out == {"ok": True, "mode": "ready", "state": "IDLE", "results": []}
    assert github.listed == 1
    assert github.timeline_reads == 0
    assert github.checked == 0
    assert github.marked == 0


def test_open_non_draft_pull_request_gets_no_timeline_read(
    sweep: Callable[..., Any],
) -> None:
    # An open pull request that is not a draft has nothing to ready.
    github = GitHub(drafts={7: False})
    out = sweep(github, {PHASE: _receipt(PHASE, [PHASE])})
    assert out == {"ok": True, "mode": "ready", "state": "IDLE", "results": []}
    assert github.listed == 1
    assert github.timeline_reads == 0
    assert github.checked == 0
    assert github.marked == 0


def test_open_non_draft_issue_pull_request_gets_no_timeline_read(
    sweep: Callable[..., Any],
) -> None:
    github = GitHub(drafts={7: False})
    rows = [{"issue": 77, "branch": "issue/77-task", "pull_request": PULL_REQUEST}]
    out = sweep(github, issue_rows=rows)
    assert out["results"] == []
    assert github.listed == 1
    assert github.timeline_reads == 0
    assert github.marked == 0


def test_merged_issue_pull_request_is_never_read(sweep: Callable[..., Any]) -> None:
    github = GitHub(drafts={})
    rows = [{"issue": 77, "branch": "issue/77-task", "pull_request": PULL_REQUEST}]
    out = sweep(github, issue_rows=rows)
    assert out["results"] == []
    assert github.listed == 1
    assert github.timeline_reads == 0
    assert github.marked == 0


def test_open_draft_pull_request_is_read_and_readied(sweep: Callable[..., Any]) -> None:
    github = GitHub(drafts={7: True})
    out = sweep(github, {PHASE: _receipt(PHASE, [PHASE])})
    assert out["state"] == "READY"
    assert github.timeline_reads == 1
    assert github.marked == 1


def test_group_lists_open_pull_requests_once_and_reads_it_once(
    sweep: Callable[..., Any],
) -> None:
    github = GitHub(drafts={7: True})
    receipts = {
        PHASE: _receipt(PHASE, [PHASE, SIBLING]),
        SIBLING: _receipt(SIBLING, [PHASE, SIBLING]),
    }
    out = sweep(github, receipts)
    assert out["state"] == "READY"
    assert github.listed == 1
    assert github.timeline_reads == 1
    assert github.marked == 1


def test_unreadable_open_list_is_held_and_nothing_is_read(
    sweep: Callable[..., Any],
) -> None:
    # A failed list read must not look like "no pull requests": it is held.
    github = GitHub(drafts=None, why="gh did not answer within 15s")
    out = sweep(github, {PHASE: _receipt(PHASE, [PHASE])})
    assert out["ok"] is False
    assert out["state"] == "HELD"
    assert "could not be read" in out["results"][0]["detail"]
    assert github.listed == 1
    assert github.timeline_reads == 0
    assert github.marked == 0


def test_unreadable_open_list_leaves_issue_pull_request_unready(
    sweep: Callable[..., Any],
) -> None:
    github = GitHub(drafts=None, why="gh returned JSON that is not a list")
    rows = [{"issue": 77, "branch": "issue/77-task", "pull_request": PULL_REQUEST}]
    out = sweep(github, issue_rows=rows)
    assert out["results"][0]["state"] == "WAITING"
    assert github.timeline_reads == 0
    assert github.marked == 0


def _gh_reply(
    monkeypatch: pytest.MonkeyPatch, stdout: str, argv_out: list[Any]
) -> None:
    """Answer the one gh list call with ``stdout`` and record its argument list."""
    monkeypatch.setattr(
        conventions, "_settings", {"commands.gh": "gh", "commands.git": "git"}
    )

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv_out.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(gh_open.subprocess, "run", fake_run)


def test_open_list_maps_each_pull_request_to_its_draft_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    argv: list[Any] = []
    rows = [{"number": 7, "isDraft": True}, {"number": 9, "isDraft": False}]
    _gh_reply(monkeypatch, json.dumps(rows), argv)
    drafts, why = gh_open.gh_open_pull_request_numbers(
        tmp_path, "example-org", "example-repo", None
    )
    assert (drafts, why) == ({7: True, 9: False}, "")
    assert "number,isDraft" in argv


def test_open_list_row_without_draft_state_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gh_reply(monkeypatch, json.dumps([{"number": 7}]), [])
    drafts, why = gh_open.gh_open_pull_request_numbers(
        tmp_path, "example-org", "example-repo", None
    )
    assert drafts is None
    assert "draft state" in why
