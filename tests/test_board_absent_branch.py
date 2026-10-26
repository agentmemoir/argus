# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A remote with no claims branch reads as an empty board; an unreachable one fails."""

import shutil
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.git import _git
from argus.board.selftest_fixture import _git_test_repo
from argus.board.state import _empty_state
from argus.board.store import ReservationStore

BRANCH = "fixture-board"
STATE = "coordination/phases.json"


def _configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: str, board_repo: str
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    settings: dict[str, object] = {
        "repo-root": project,
        "board.repo": tmp_path / board_repo,
        "board.branch": BRANCH,
        "board.remote": "origin",
        "board.file": STATE,
        "commands.git": git,
    }
    monkeypatch.setattr(conventions, "_settings", settings)
    return project


@pytest.fixture
def git_exe() -> str:
    found = shutil.which("git")
    assert found is not None
    return found


def test_a_remote_without_the_claims_branch_reads_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "absent-client-1")
    _git_test_repo(tmp_path, "absent")
    store = ReservationStore(project)
    assert store.read() == (_empty_state(), "")
    # The first write creates the branch, through the existing write path.
    assert ReservationStore(project).migrate_board() is True


def test_a_deleted_board_with_its_backup_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "deleted-client-1")
    _git_test_repo(tmp_path, "deleted")
    assert ReservationStore(project).migrate_board() is True
    # The board branch is gone, but its backup remains, so the board existed.
    _git(
        project.parent / "deleted-client-1", "push", "-q", "origin", "--delete", BRANCH
    )
    with pytest.raises(CoordinationUnavailable):
        ReservationStore(project).read()


def test_an_unreachable_remote_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "lost-client-1")
    remote, _clients = _git_test_repo(tmp_path, "lost")
    shutil.rmtree(remote)
    with pytest.raises(CoordinationUnavailable):
        ReservationStore(project).read()
