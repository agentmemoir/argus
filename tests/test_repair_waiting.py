# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Which open driver pull requests still need the driver, from the reads a tick makes."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import repair_cli

GREEN: list[dict[str, Any]] = [{"name": "test", "bucket": "pass", "link": ""}]
PENDING: list[dict[str, Any]] = [{"name": "test", "bucket": "pending", "link": ""}]
RED: list[dict[str, Any]] = [{"name": "lint", "bucket": "fail", "link": ""}]
PR = "https://github.com/o/r/pull/7"
HEAD = "a" * 40


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    rows: list[dict[str, Any]],
    mergeable: str,
    draft: bool,
) -> dict[str, Any]:
    def checks(*_a: object) -> tuple[list[dict[str, Any]], int, str]:
        return rows, 1, ""

    monkeypatch.setattr(repair_cli, "gh_pull_request_checks", checks)
    monkeypatch.setattr(repair_cli, "gh_pull_request_mergeable", lambda *_a: mergeable)
    monkeypatch.setattr(repair_cli, "gh_pull_request_head", lambda *_a: HEAD)
    argv = ["--pull-request", PR, "--run-dir", str(tmp_path)]
    argv += ["--attempts", "2", "--wait", "600", "--head", HEAD]
    argv += ["--draft", "yes" if draft else "no"]
    assert repair_cli.main(argv, now=1000.0) == 0
    return json.loads(capsys.readouterr().out)


def test_a_draft_with_pending_checks_waits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = _run(monkeypatch, capsys, tmp_path, PENDING, "MERGEABLE", draft=True)
    assert out["action"] == "none"
    assert out["waiting"] == "in draft, checks pending"


def test_a_draft_with_green_checks_waits_for_its_ready_mark(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = _run(monkeypatch, capsys, tmp_path, GREEN, "MERGEABLE", draft=True)
    assert out["waiting"] == "in draft, checks passing"


def test_a_pull_request_ready_for_review_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = _run(monkeypatch, capsys, tmp_path, GREEN, "MERGEABLE", draft=False)
    assert "waiting" not in out


def test_a_draft_past_the_pending_limit_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The pending limit's Needs You notice already went out for this head, so the
    # draft is escalated and must not stall selection.
    record = {"head": HEAD, "since": 0.0, "noticed": [HEAD], "open": HEAD}
    (tmp_path / "pending-7.json").write_text(json.dumps(record), encoding="utf-8")
    out = _run(monkeypatch, capsys, tmp_path, PENDING, "MERGEABLE", draft=True)
    assert out["action"] == "none"
    assert "waiting" not in out


def test_a_failing_check_within_budget_waits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = _run(monkeypatch, capsys, tmp_path, RED, "MERGEABLE", draft=False)
    assert out["action"] == "repair"
    assert out["waiting"] == "with failing checks, repair budget left"


def test_a_conflict_within_budget_waits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = _run(monkeypatch, capsys, tmp_path, GREEN, "CONFLICTING", draft=False)
    assert out["action"] == "repair"
    assert out["waiting"] == "conflicting with base, repair budget left"


def test_an_escalated_failing_draft_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Two rounds already counted for this head: the next tick escalates.
    record = {"head": HEAD, "attempts": [0.0, 1.0]}
    (tmp_path / "repair-7.json").write_text(json.dumps(record), encoding="utf-8")
    out = _run(monkeypatch, capsys, tmp_path, RED, "MERGEABLE", draft=True)
    assert out["action"] == "escalate"
    assert "waiting" not in out


def test_an_issue_listing_without_a_draft_flag_is_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "issue-7").mkdir()
    monkeypatch.setattr(repair_cli.conventions, "runs_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        repair_cli, "issue_scope", lambda *_a: (None, (None, "issue/7-x"))
    )
    monkeypatch.setattr(repair_cli, "issue_branch", lambda *_a: "issue/7-x")
    listed = [{"number": 7, "url": PR, "state": "OPEN", "isDraft": None}]
    monkeypatch.setattr(
        repair_cli, "gh_pull_requests_for_branch", lambda *_a: (listed, "")
    )
    unread: list[int] = []
    assert repair_cli.issue_pull_requests(tmp_path, unread) == []
    assert unread == [7]


def test_an_issue_listing_carries_the_draft_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "issue-7").mkdir()
    monkeypatch.setattr(repair_cli.conventions, "runs_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        repair_cli, "issue_scope", lambda *_a: (None, (None, "issue/7-x"))
    )
    monkeypatch.setattr(repair_cli, "issue_branch", lambda *_a: "issue/7-x")
    listed = [{"number": 7, "url": PR, "state": "OPEN", "isDraft": True}]
    monkeypatch.setattr(
        repair_cli, "gh_pull_requests_for_branch", lambda *_a: (listed, "")
    )
    rows = repair_cli.issue_pull_requests(tmp_path)
    assert rows == [
        {"issue": 7, "branch": "issue/7-x", "pull_request": PR, "draft": True}
    ]
