# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair command entry: what one tick prints and records per pull request."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import gh_read, repair_cli
from argus.gitsync import git as gitsync_git

RED: list[dict[str, Any]] = [{"name": "lint", "bucket": "fail", "link": "https://ci/1"}]
PR = "https://github.com/o/r/pull/7"
SAME: dict[str, Any] = {"headRefName": "issue/7-fix-ci", "isCrossRepository": False}
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    rows: list[dict[str, Any]] | None,
    now: float,
    launched: bool = True,
    head: str = HEAD_A,
) -> tuple[int, dict[str, Any]]:
    def checks(*_a: object) -> tuple[list[dict[str, Any]] | None, int, str]:
        return rows, 1, ""

    monkeypatch.setattr(repair_cli, "gh_pull_request_checks", checks)

    def merge(*_a: object) -> str:
        return "MERGEABLE"

    monkeypatch.setattr(repair_cli, "gh_pull_request_mergeable", merge)
    argv = ["--pull-request", PR, "--run-dir", str(tmp_path)]
    argv += ["--attempts", "2", "--wait", "600", "--head", head]
    rc = repair_cli.main(argv, now=now)
    out = json.loads(capsys.readouterr().out)
    if launched and out["action"] == "repair":
        assert repair_cli.main([*argv, "--launched"], now=now) == 0
        assert json.loads(capsys.readouterr().out) == {"action": "counted"}
    return rc, out


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


def test_a_new_head_gets_a_fresh_budget_and_the_escalated_head_stays_stopped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    for now in (1000.0, 1600.0):
        _rc, out = _run(monkeypatch, capsys, tmp_path, RED, now)
        assert out["action"] == "repair"
    _rc, out = _run(monkeypatch, capsys, tmp_path, RED, 9999.0)
    assert out["action"] == "escalate"
    assert _run(monkeypatch, capsys, tmp_path, RED, 9999.0)[1] == {"action": "none"}
    _rc, out = _run(monkeypatch, capsys, tmp_path, RED, 9999.0, head=HEAD_B)
    assert out["action"] == "repair"


def test_a_round_that_never_launched_is_never_counted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    for now in (1000.0, 1600.0, 9999.0):
        _rc, out = _run(monkeypatch, capsys, tmp_path, RED, now, launched=False)
        assert out["action"] == "repair"
    assert not (tmp_path / "repair-7.json").exists()


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
    assert (
        repair_cli.main([*argv, "--attempts", "1", "--wait", "0", "--head", HEAD_A])
        == 2
    )
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


def test_an_unreadable_record_names_its_cause_and_starts_no_round(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    (tmp_path / "repair-7.json").write_text("{not json", encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, tmp_path, RED, 1000.0, launched=False)
    assert (rc, out["action"]) == (0, "unreadable")
    assert out["message"].startswith(
        f"Needs You. The attempt record repair-7.json of {PR}"
    )
    assert "no repair round" in out["message"]


PENDING_ROWS: list[dict[str, Any]] = [{"name": "CI", "bucket": "pending", "link": ""}]
REPAIR_ARGV = [
    "--pull-request",
    PR,
    "--run-dir",
    "",
    "--attempts",
    "2",
    "--wait",
    "600",
    "--head",
    HEAD_A,
]


def _gh_head(head: str | None) -> Any:
    """A gh stub: the one pr view answers with the head, or fails when head is None."""

    def run(argv: list[str], **_kw: Any) -> Any:
        assert argv[1:3] == ["pr", "view"], argv
        if head is None:
            return type("Done", (), {"returncode": 1, "stdout": ""})()
        payload = json.dumps({"headRefOid": head})
        return type("Done", (), {"returncode": 0, "stdout": payload})()

    return run


def _pending_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    head: str | None,
    now: float,
) -> tuple[int, dict[str, Any]]:
    def checks(*_a: object) -> tuple[list[dict[str, Any]] | None, int, str]:
        return PENDING_ROWS, 8, ""

    real_setting = gitsync_git.setting

    def setting(key: str) -> Any:
        return "gh" if key == "commands.gh" else real_setting(key)

    monkeypatch.setattr(repair_cli, "gh_pull_request_checks", checks)

    def merge(*_a: object) -> str:
        return "MERGEABLE"

    monkeypatch.setattr(repair_cli, "gh_pull_request_mergeable", merge)
    monkeypatch.setattr(gitsync_git, "setting", setting)
    monkeypatch.setattr(repair_cli.subprocess, "run", _gh_head(head))
    argv = [*REPAIR_ARGV[:3], str(tmp_path), *REPAIR_ARGV[4:]]
    rc = repair_cli.main(argv, now=now)
    return rc, json.loads(capsys.readouterr().out)


def test_a_pending_check_with_an_unread_head_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc, out = _pending_run(monkeypatch, capsys, tmp_path, None, 1000.0)
    assert (rc, out["action"], out["read"]) == (3, "unread", "head")
    assert not (tmp_path / "pending-7.json").exists()


def test_a_pending_check_names_its_notice_once_past_the_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    head = "d" * 40
    _rc, first = _pending_run(monkeypatch, capsys, tmp_path, head, 1000.0)
    assert first == {"action": "none"}
    _rc, second = _pending_run(monkeypatch, capsys, tmp_path, head, 2801.0)
    assert second["notice"].startswith(f"Needs You. {PR} has a check pending")
    _rc, third = _pending_run(monkeypatch, capsys, tmp_path, head, 3600.0)
    assert "notice" not in third


def test_an_unread_issue_pull_request_is_listed_for_the_caller(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    (runs / "issue-9").mkdir(parents=True)
    tasks = ["runs/issue-9", "tasks/issue-9-docs.md"]

    def runs_dir(_root: object) -> Path:
        return runs

    def scope(_r: object, _n: int) -> tuple[Path, list[str]]:
        return tmp_path, tasks

    def listed(_r: object, _b: str) -> tuple[None, str]:
        return None, "detail"

    monkeypatch.setattr(repair_cli.conventions, "runs_dir", runs_dir)
    monkeypatch.setattr(repair_cli, "issue_scope", scope)
    monkeypatch.setattr(repair_cli, "gh_pull_requests_for_branch", listed)
    unread: list[int] = []
    assert repair_cli.issue_pull_requests(tmp_path, unread) == []
    assert unread == [9]
    assert "issue 9" in capsys.readouterr().err
