# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Seeding the board branch: an empty board is valid, a deleted state file is not."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.git import _git, git_binary
from argus.board.selftest_fixture import _git_test_repo
from argus.board.state import _empty_state, _write_state_file, state_rel
from argus.board.store import ReservationStore

BRANCH = "fixture-board"
STATE = "coordination/phases.json"
ISSUES_STATE = "coordination/issues.json"


@pytest.fixture
def git_exe() -> str:
    executable = shutil.which("git")
    assert executable is not None
    return executable


def _configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: str, board_repo: str
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": project,
            "board.repo": tmp_path / board_repo,
            "board.branch": BRANCH,
            "board.remote": "origin",
            "board.file": STATE,
            "board.issues-file": ISSUES_STATE,
            "commands.git": git,
        },
    )
    return project


def _remove_and_push(client: Path, relative: str) -> None:
    assert _git(client, "rm", "-q", relative)[0] == 0
    assert _git(client, "commit", "-q", "-m", "remove state")[0] == 0
    assert _git(client, "push", "-q", "origin", "HEAD:master")[0] == 0


def _ref_commit(repo: Path, ref: str) -> str | None:
    rc, out, _err = _git(repo, "rev-parse", "--verify", ref)
    return out.strip() if rc == 0 else None


def test_seeding_an_empty_board_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "empty-client-1")
    _git_test_repo(tmp_path, "empty")
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.read()[0] == _empty_state()


def test_seeding_refuses_a_state_file_removed_from_the_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "broken-client-1")
    _remote, clients = _git_test_repo(tmp_path, "broken", initial_state=_empty_state())
    _remove_and_push(clients[0], state_rel().as_posix())
    store = ReservationStore(project)
    with pytest.raises(CoordinationUnavailable, match="would read as broken"):
        store.migrate_board()


def test_seeding_refuses_a_shallow_board_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "shallow-client")
    _git_test_repo(tmp_path, "shallow", initial_state=_empty_state())
    # A local path clone ignores --depth, so the shallow copy needs file://.
    subprocess.run(
        [
            git_binary(),
            "clone",
            "--depth",
            "1",
            "-q",
            (tmp_path / "shallow-remote.git").as_uri(),
            str(tmp_path / "shallow-client"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    store = ReservationStore(project)
    with pytest.raises(CoordinationUnavailable, match="shallow"):
        store.migrate_board()


def test_a_removed_phase_file_does_not_block_the_issue_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "mixed-client-1")
    _remote, clients = _git_test_repo(tmp_path, "mixed", initial_state=_empty_state())
    _remove_and_push(clients[0], state_rel().as_posix())
    issues = ReservationStore(project, issues=True)
    assert issues.migrate_board() is True


def test_a_removed_issue_file_refuses_the_issue_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "issues-broken-client-1")
    _remote, clients = _git_test_repo(tmp_path, "issues-broken")
    issues_rel = state_rel(issues=True).as_posix()
    _write_state_file(clients[0] / issues_rel, {"version": 1})
    assert _git(clients[0], "add", issues_rel)[0] == 0
    assert _git(clients[0], "commit", "-q", "-m", "add issues state")[0] == 0
    assert _git(clients[0], "push", "-q", "origin", "HEAD:master")[0] == 0
    _remove_and_push(clients[0], issues_rel)
    issues = ReservationStore(project, issues=True)
    with pytest.raises(CoordinationUnavailable, match="would read as broken"):
        issues.migrate_board()


def test_a_lost_board_branch_is_reseeded_from_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "recover-client-1")
    remote, _clients = _git_test_repo(tmp_path, "recover", initial_state=_empty_state())
    store = ReservationStore(project)
    assert store.migrate_board() is True
    record = store.reserve(100020, "server-01")
    # Every write advances the backup with the board it mirrors.
    assert _ref_commit(remote, f"refs/heads/{BRANCH}-backup") == _ref_commit(
        remote, f"refs/heads/{BRANCH}"
    )
    # Lose the board branch; the backup is now the only durable copy.
    assert _git(remote, "update-ref", "-d", f"refs/heads/{BRANCH}")[0] == 0
    assert _ref_commit(remote, f"refs/heads/{BRANCH}") is None
    # The next migration restores the board from the backup, not from a clone.
    assert store.migrate_board() is True
    state, _commit = store.read()
    assert state["phases"]["100020"]["reservation_id"] == record["reservation_id"]


def test_recovery_prefers_the_backup_over_a_broken_integration_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "recover-broken-client-1")
    remote, clients = _git_test_repo(
        tmp_path, "recover-broken", initial_state=_empty_state()
    )
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.reserve(100021, "server-01")
    # After the board moved, the state file leaves the integration tip. A
    # reseed from that tip alone would refuse; the backup must save it.
    _remove_and_push(clients[0], state_rel().as_posix())
    assert _git(remote, "update-ref", "-d", f"refs/heads/{BRANCH}")[0] == 0
    assert store.migrate_board() is True
    state, _commit = store.read()
    assert "100021" in state["phases"]


def test_cli_module_runs_and_reports_a_missing_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "argus.board.cli"],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "a command is required" in result.stderr


def test_cli_module_serves_the_show_subcommand(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "argus.board.cli", "show", "--help"],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: cli.py show" in result.stdout
    assert "-h, --help" in result.stdout
