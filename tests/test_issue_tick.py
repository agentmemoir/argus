# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One real issue tick claims on a fixture board and launches only the stub."""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from issues_fixture import argv, fake_gh, fixture_update, issue

ROOT = Path(__file__).resolve().parents[1]
MACHINE = "tick-machine"
PIPELINE = """version: 1
run_dir: board/runs/issue-{issue_number:06d}
inputs:
  issue_number: {type: int, required: true}
  issue_file: {required: true}
start: fixture
ends: [completed]
steps:
  fixture:
    stage: stage.md
    bindings: {}
    end: completed
"""
STAGE = "---\nname: Fixture\nharness: hermes:fixture\nplaceholders: {}\n---\nStage.\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    ).stdout


def checkout(remote: Path, clone: Path, files: dict[str, str]) -> None:
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(remote)],
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(clone)], check=True, timeout=30
    )
    git(clone, "config", "user.email", "fixture@example.com")
    git(clone, "config", "user.name", "fixture")
    git(clone, "checkout", "-q", "-b", "master")
    for name, text in files.items():
        (clone / name).parent.mkdir(parents=True, exist_ok=True)
        (clone / name).write_text(text, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "init")
    git(clone, "push", "-q", "-u", "origin", "master")


@pytest.fixture
def tmux_dir() -> Iterator[Path]:
    # A short private socket folder: macOS limits the socket path length, and
    # the tick's tmux server must never be the operator's.
    with tempfile.TemporaryDirectory(prefix="ag-tick-", dir="/tmp") as folder:
        yield Path(folder)
        subprocess.run(
            ["tmux", "kill-server"],
            env={**os.environ, "TMUX_TMPDIR": folder},
            capture_output=True,
            timeout=15,
            check=False,
        )


@pytest.fixture
def backlog(tmp_path: Path) -> Path:
    """A public checkout, a private board checkout, a fake gh and a config."""
    root, board = tmp_path / "root", tmp_path / "root" / "board"
    checkout(tmp_path / "pub.git", root, {"a.txt": "one\n"})
    (root / ".git/info/exclude").write_text("board/\n", encoding="utf-8")
    checkout(
        tmp_path / "priv.git",
        board,
        {
            "flow.yaml": PIPELINE,
            "stage.md": STAGE,
            ".gitignore": "__pycache__/\n",
            "tasks/.keep": "",
            "runs/.keep": "",
        },
    )
    gh = fake_gh(tmp_path, [issue(100001)])
    stub = tmp_path / "glide"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    (tmp_path / "machine.id").write_text(MACHINE + "\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "repo-root": str(root),
                "pipeline": str(board / "flow.yaml"),
                "roadmap-dir": str(board),
                "number-input": "issue_number",
                "file-input": "issue_file",
                "driver": {
                    "provider": "issues",
                    "include": ["phase-*.md"],
                    "runs": str(board / "runs"),
                    "state": "driver-state",
                    "stale-after": 2700,
                    "outage-stale": 32400,
                    "quiet-minutes": 45,
                    "keep-logs-days": 7,
                    "merge-check-max": 5,
                    "recover-own-claim": False,
                },
                "machine": {"id-file": str(tmp_path / "machine.id")},
                "commands": {
                    "glide": str(stub),
                    "git": "git",
                    "tmux": "tmux",
                    "python": sys.executable,
                    "hermes": str(stub),
                    "gh": str(gh),
                },
                "tmux": {
                    "session": "issue-tick",
                    "width": 164,
                    "height": 56,
                    "idle-window": "idle",
                    "phase-window": "phase",
                    "idle-script": "phase-tmux-idle.sh",
                    "refresh": 15,
                },
                "board": {
                    "repo": str(board),
                    "file": "claims.json",
                    "issues-file": "issues.json",
                    "branch": "fixture-claims",
                    "remote": "origin",
                },
                "issues": {
                    "repo": "example/project",
                    "label-groups": {
                        "type": {"labels": ["bug"], "cardinality": "one"},
                        "area": {"labels": ["area:cli"], "cardinality": "many"},
                        "difficulty": {
                            "labels": ["difficulty:easy"],
                            "cardinality": "one",
                        },
                    },
                    "difficulty-order": ["difficulty:easy"],
                    "preference": "newest",
                    "task-dir": str(board / "tasks"),
                    "announce": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def argus(base: Path, tmux_dir: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "bin/argus"), "--config", str(base / "config.json"), *flags],
        cwd=base / "root",
        env={
            **os.environ,
            "TMUX_TMPDIR": str(tmux_dir),
            "DISABLE_NOTIFY": "1",
            "DRIVER_STUB_RC": "0",
            # The stub waits for a file nobody writes, so the launched window
            # holds still while the board is read; the fixture kills it after.
            "DRIVER_STUB_WAIT_FILE": str(base / "never"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def test_issue_tick_claims_one_issue_and_launches_only_the_stub(
    backlog: Path, tmux_dir: Path
) -> None:
    board = backlog / "root/board"
    tick = argus(backlog, tmux_dir)
    log_path = board / "runs/driver-state/driver.log"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert tick.returncode == 0, (tick.stderr, log)
    assert "reserved issue 100001 (board/tasks/" in log, log

    git(board, "fetch", "-q", "origin")
    claims = json.loads(git(board, "show", "origin/fixture-claims:issues.json"))
    record = claims["phases"]["100001"]
    assert (record["machine_id"], record["run_id"]) == (MACHINE, "issue-100001")
    phases = subprocess.run(
        [
            "git",
            "-C",
            str(board),
            "cat-file",
            "-e",
            "origin/fixture-claims:claims.json",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert phases.returncode != 0, "an issue claim must not create a phase record"

    task = next((board / "tasks").glob("issue-100001-*.md"))
    assert "<<<UNTRUSTED-DATA-BEGIN id=issue-100001-body" in task.read_text("utf-8")
    calls = [call["argv"] for call in argv(backlog)]
    assert [
        "issue",
        "edit",
        "100001",
        "--repo",
        "example/project",
        "--add-assignee",
        "@me",
    ] in calls
    assert not any(call["remote_in_env"] for call in argv(backlog))


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ("HTTP 401: Bad credentials", "token"),
        ("API rate limit exceeded for user", "rate_limit"),
        ("dial tcp: lookup api.github.com: no such host", "network"),
    ],
)
def test_readiness_report_names_the_github_failure(
    backlog: Path, tmux_dir: Path, error: str, reason: str
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/argus/select/next_phase.py"),
            "--config",
            str(backlog / "config.json"),
            "--source",
            "issues",
            "--migrate-board",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=True,
    )
    fixture_update(backlog, mode="failure", error=error)
    report = argus(backlog, tmux_dir, "--check")
    assert report.returncode != 0, report.stdout
    refused = [
        line
        for line in report.stdout.splitlines()
        if "issue readiness: REFUSED" in line
    ]
    assert len(refused) == 1 and f"issues {reason}:" in refused[0], report.stdout
    assert error not in report.stdout, "gh's own text must not reach the report"


def test_phase_machine_without_a_board_still_refuses_cleanly() -> None:
    # The issue board threading must not change how a phase machine with no
    # board is refused: a typed result, never a traceback.
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/argus/select/next_phase.py"),
            "--config",
            str(ROOT / "examples/argus-config.jsonc"),
            "--source",
            "roadmap",
            "--server",
            "--machine-id",
            MACHINE,
            "--json",
            "--read-only",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout) == {
        "error": "reservation board repository is not configured",
        "state": "COORDINATION_UNAVAILABLE",
    }
