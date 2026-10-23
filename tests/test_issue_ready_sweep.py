# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The ready sweep for issue pull requests marks one ready only when all checks pass.

GitHub is replaced at the group transition's own call sites, so the test runs
the real sweep, the real checks gate, and the real ready/draft history guard.
"""

from pathlib import Path
from typing import Any

import pytest

import argus.gitsync.group_review as group_review
from argus.gitsync import modes

PULL_REQUEST = "https://github.com/agentmemoir/argus/pull/77"
DRAFT: dict[str, Any] = {"state": "OPEN", "is_draft": True, "events": []}
READIED: dict[str, Any] = {
    "state": "OPEN",
    "is_draft": False,
    "events": [{"type": "ReadyForReviewEvent"}],
}
GREEN = [{"bucket": "pass", "state": "SUCCESS", "name": "ci", "link": ""}]


class FakeGitHub:
    """The three gh calls the transition makes, and the ready event they record."""

    def __init__(self) -> None:
        self.stage: dict[str, Any] = DRAFT
        self.rows: list[dict[str, Any]] | None = GREEN
        self.code = 0
        self.checked = 0
        self.marked = 0

    def history(self, *_args: Any) -> tuple[dict[str, Any], str]:
        return self.stage, ""

    def checks(self, *_args: Any, **_kwargs: Any) -> tuple[Any, int, str]:
        self.checked += 1
        return self.rows, self.code, ""

    def mark(self, *_args: Any) -> tuple[bool, str]:
        self.marked += 1
        self.stage = READIED  # GitHub records the ready event the next read sees
        return True, ""


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(group_review, "gh_pull_request_stage_history", fake.history)
    monkeypatch.setattr(group_review, "gh_pull_request_checks", fake.checks)
    monkeypatch.setattr(group_review, "gh_mark_pull_request_ready", fake.mark)
    return fake


def _no_phase_runs(_root: Path) -> list[Any]:
    return []


def _one_issue_pull_request(_root: Path) -> list[dict[str, Any]]:
    return [{"issue": 77, "branch": "issue/77-task", "pull_request": PULL_REQUEST}]


@pytest.fixture
def run_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(modes, "_phase_run_entries", _no_phase_runs)
    monkeypatch.setattr(modes, "issue_pull_requests", _one_issue_pull_request)
    folder = tmp_path / "run"
    folder.mkdir()
    return folder


def sweep(tmp_path: Path, run_dir: Path) -> dict[str, Any]:
    return modes.mode_ready(tmp_path, run_dir)


def test_green_draft_issue_pull_request_is_readied_once(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    out = sweep(tmp_path, run_dir)
    assert out["state"] == "READY"
    assert [r["kind"] for r in out["results"]] == ["issue"]
    assert gh.marked == 1
    again = sweep(tmp_path, run_dir)
    assert gh.marked == 1, "a second tick must not ready the pull request again"
    assert again["results"][0]["skipped"] is True


def test_pending_check_keeps_issue_pull_request_draft(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    gh.rows = [{"bucket": "pending", "state": "IN_PROGRESS", "name": "ci", "link": ""}]
    out = sweep(tmp_path, run_dir)
    assert out["results"][0]["state"] == "WAITING"
    assert gh.marked == 0


def test_failing_check_keeps_issue_pull_request_draft(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    gh.rows = [{"bucket": "fail", "state": "FAILURE", "name": "ci", "link": ""}]
    out = sweep(tmp_path, run_dir)
    assert out["results"][0]["state"] == "WAITING"
    assert gh.marked == 0


def test_zero_check_rows_keep_issue_pull_request_draft(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    gh.rows = []
    out = sweep(tmp_path, run_dir)
    assert out["results"][0]["state"] == "WAITING"
    assert gh.marked == 0


def test_running_issue_run_is_skipped_without_a_github_call(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    (run_dir / "current").write_text("77\n", encoding="utf-8")
    out = sweep(tmp_path, run_dir)
    assert out["results"] == []
    assert gh.checked == 0 and gh.marked == 0


def test_running_repair_round_is_skipped_without_a_github_call(
    gh: FakeGitHub, run_dir: Path, tmp_path: Path
) -> None:
    (run_dir / "repair-worktree-77").mkdir()
    out = sweep(tmp_path, run_dir)
    assert out["results"] == []
    assert gh.checked == 0 and gh.marked == 0


def test_no_run_folder_means_no_issue_ready(
    gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(modes, "_phase_run_entries", _no_phase_runs)
    out = modes.mode_ready(tmp_path)
    assert out["results"] == []
    assert gh.checked == 0 and gh.marked == 0
