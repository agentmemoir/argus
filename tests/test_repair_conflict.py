# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair command when a pull request conflicts with its base."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import repair_cli

GREEN: list[dict[str, Any]] = [
    {"name": "test", "bucket": "pass", "link": "https://ci/2"}
]
RED: list[dict[str, Any]] = [{"name": "lint", "bucket": "fail", "link": "https://ci/1"}]
PR = "https://github.com/o/r/pull/7"
HEAD = "a" * 40


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    rows: list[dict[str, Any]],
    mergeable: str | None,
    now: float,
    launched: bool = True,
) -> tuple[int, dict[str, Any]]:
    def checks(*_a: object) -> tuple[list[dict[str, Any]], int, str]:
        return rows, 1, ""

    monkeypatch.setattr(repair_cli, "gh_pull_request_checks", checks)
    monkeypatch.setattr(repair_cli, "gh_pull_request_mergeable", lambda *_a: mergeable)
    argv = ["--pull-request", PR, "--run-dir", str(tmp_path)]
    argv += ["--attempts", "2", "--wait", "600", "--head", HEAD]
    rc = repair_cli.main(argv, now=now)
    out = json.loads(capsys.readouterr().out)
    if launched and out["action"] == "repair":
        assert repair_cli.main([*argv, "--launched"], now=now) == 0
        capsys.readouterr()
    return rc, out


def test_a_conflict_starts_a_round_with_green_checks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, GREEN, "CONFLICTING", 1000.0)
    assert (rc, out["action"]) == (0, "repair")
    task = Path(out["task"]).read_text(encoding="utf-8")
    assert "# Repair https://github.com/o/r/pull/7: it conflicts with base" in task


def test_an_unknown_merge_state_starts_no_round_when_checks_are_green(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, GREEN, "UNKNOWN", 1000.0)
    assert (rc, out) == (0, {"action": "none"})
    assert not list(tmp_path.glob("repair-*"))


def test_an_unknown_merge_state_still_repairs_red_ci(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, RED, "UNKNOWN", 1000.0)
    assert (rc, out["action"]) == (0, "repair")
    task = Path(out["task"]).read_text(encoding="utf-8")
    assert "- lint: https://ci/1" in task
    assert "conflicts with base" not in task


def test_an_unread_merge_state_fails_closed_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, RED, None, 1000.0)
    assert (rc, out) == (3, {"action": "unread", "read": "mergeable"})
    assert list(tmp_path.iterdir()) == []


def test_conflict_rounds_count_against_the_same_budget(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _rc, out = _run(monkeypatch, capsys, tmp_path, GREEN, "CONFLICTING", 1000.0)
    assert out["action"] == "repair"
    _rc, out = _run(monkeypatch, capsys, tmp_path, RED, "MERGEABLE", 1600.0)
    assert out["action"] == "repair"
    _rc, out = _run(monkeypatch, capsys, tmp_path, GREEN, "CONFLICTING", 9999.0)
    assert out["action"] == "escalate"
    assert out["message"].startswith(
        f"Needs You. {PR} still conflicts with its base after 2"
    )
    _rc, out = _run(monkeypatch, capsys, tmp_path, GREEN, "CONFLICTING", 9999.0)
    assert out == {"action": "none"}
