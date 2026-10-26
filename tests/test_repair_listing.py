# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The issue listing the repair tick reads: recorded branches and forks."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import gh_read, repair_cli

SAME: dict[str, Any] = {
    "headRefName": "issue/7-fix-ci",
    "isCrossRepository": False,
    "isDraft": False,
}


def test_issue_pull_requests_reads_only_recorded_branches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    for name in ("issue-7", "issue-8", "issue-9", "issue-x"):
        (runs / name).mkdir(parents=True)
    tasks = {7: "tasks/issue-7-fix-ci.md", 9: "tasks/issue-9-docs.md"}

    def scope(_root: object, number: int) -> tuple[Path, list[str]]:
        if number not in tasks:
            raise repair_cli.ConfigError("current issue has no valid frozen task")
        return tmp_path, [f"runs/issue-{number}", tasks[number]]

    def listed(_root: Path, branch: str) -> tuple[list[dict[str, Any]] | None, str]:
        if branch == "issue/9-docs":
            return None, "gh secret detail"
        assert branch == "issue/7-fix-ci"
        return [
            {"number": 70, "url": "https://g/o/r/pull/70", "state": "OPEN", **SAME},
            {"number": 60, "url": "https://g/o/r/pull/60", "state": "CLOSED", **SAME},
        ], ""

    def runs_dir(_root: object) -> Path:
        return runs

    monkeypatch.setattr(repair_cli.conventions, "runs_dir", runs_dir)
    monkeypatch.setattr(repair_cli, "issue_scope", scope)
    monkeypatch.setattr(repair_cli, "gh_pull_requests_for_branch", listed)
    rows = repair_cli.issue_pull_requests(tmp_path)
    assert rows == [
        {
            "issue": 7,
            "branch": "issue/7-fix-ci",
            "pull_request": "https://g/o/r/pull/70",
            "draft": False,
        }
    ]
    err = capsys.readouterr().err
    assert "issue 9" in err and "secret" not in err


def test_issue_pull_requests_ignore_a_fork_pull_request_on_the_recorded_branch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # gh --head matches the branch name only. An outsider's fork pull request
    # with the recorded name must not reach the repair round (SECURITY rule 7).
    runs = tmp_path / "runs"
    (runs / "issue-7").mkdir(parents=True)
    listed = [
        {
            "number": 90,
            "url": "https://g/o/r/pull/90",
            "state": "OPEN",
            "headRefName": "issue/7-fix-ci",
            "isCrossRepository": True,
        },
        {
            "number": 91,
            "url": "https://g/o/r/pull/91",
            "state": "OPEN",
            "headRefName": "issue/7-other",
            "isCrossRepository": False,
        },
        {"number": 70, "url": "https://g/o/r/pull/70", "state": "OPEN", **SAME},
    ]

    def gh(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, json.dumps(listed), "")

    def scope(_root: object, _number: int) -> tuple[Path, list[str]]:
        return tmp_path, ["runs/issue-7", "tasks/issue-7-fix-ci.md"]

    def runs_dir(_root: object) -> Path:
        return runs

    monkeypatch.setattr(gh_read, "gh_binary", lambda: "gh")
    monkeypatch.setattr(gh_read.subprocess, "run", gh)
    monkeypatch.setattr(repair_cli.conventions, "runs_dir", runs_dir)
    monkeypatch.setattr(repair_cli, "issue_scope", scope)
    rows = repair_cli.issue_pull_requests(tmp_path)
    # Only the same-repository row is returned as awaiting review. The fork row and
    # the other-branch row are dropped by the shared helper, and no repair-* file
    # (task or round record) is written for them.
    assert [row["pull_request"] for row in rows] == ["https://g/o/r/pull/70"]
    assert not list(tmp_path.rglob("repair-*"))
    err = capsys.readouterr().err
    assert "dropped 2 row(s)" in err and "#90" in err and "#91" in err
