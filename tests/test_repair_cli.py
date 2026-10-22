# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair command entry: what one tick prints and records per pull request."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import repair_cli

RED: list[dict[str, Any]] = [{"name": "lint", "bucket": "fail", "link": "https://ci/1"}]
PR = "https://github.com/o/r/pull/7"


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    rows: list[dict[str, Any]] | None,
    now: float,
) -> tuple[int, dict[str, Any]]:
    def checks(*_a: object) -> tuple[list[dict[str, Any]] | None, int, str]:
        return rows, 1, ""

    monkeypatch.setattr(repair_cli, "gh_pull_request_checks", checks)
    argv = ["--pull-request", PR, "--run-dir", str(tmp_path)]
    rc = repair_cli.main([*argv, "--attempts", "2", "--wait", "600"], now=now)
    return rc, json.loads(capsys.readouterr().out)


def test_red_repairs_then_waits_then_escalates_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, RED, 1000.0)
    assert (rc, out["action"]) == (0, "repair")
    assert "lint: https://ci/1" in Path(out["task"]).read_text(encoding="utf-8")
    assert _run(monkeypatch, capsys, tmp_path, RED, 1500.0)[1] == {
        "action": "wait",
        "due": 1600.0,
    }
    assert _run(monkeypatch, capsys, tmp_path, RED, 1600.0)[1]["action"] == "repair"
    _rc, out = _run(monkeypatch, capsys, tmp_path, RED, 9999.0)
    assert out["action"] == "escalate"
    assert out["message"].startswith(f"Needs You. {PR} is still red after 2")
    assert _run(monkeypatch, capsys, tmp_path, RED, 9999.0)[1] == {"action": "none"}


def test_unread_checks_fail_closed_and_record_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _run(monkeypatch, capsys, tmp_path, None, 1000.0)
    assert (rc, out["action"]) == (3, "unread")
    assert list(tmp_path.iterdir()) == []


def test_pull_request_without_number_is_refused(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    argv = ["--pull-request", "../x", "--run-dir", str(tmp_path)]
    assert repair_cli.main([*argv, "--attempts", "1", "--wait", "0"]) == 2
    assert "has no number" in capsys.readouterr().err


def test_failed_job_log_is_saved_and_its_tail_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> Any:
        calls.append(argv)
        return type("Done", (), {"returncode": 0, "stdout": "a\nboom\n"})()

    monkeypatch.setattr(repair_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(repair_cli, "gh_binary", lambda: "gh")
    link = "https://github.com/o/r/actions/runs/5/job/9"
    tail, path = repair_cli.save_failed_log(tmp_path, "7", [{"link": link}])
    assert tail == ["a", "boom"]
    assert path is not None and Path(path).read_text(encoding="utf-8") == "a\nboom\n"
    assert calls[0][-4:] == ["9", "--log-failed", "--repo", "o/r"]
    assert repair_cli.save_failed_log(tmp_path, "7", [{"link": "https://ci/1"}]) == (
        [],
        None,
    )
