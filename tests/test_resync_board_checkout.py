# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resync never merges the integration branch into the board checkout, and a
refused merge is never reported as a continued step."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import sync

IDENTITY = ["-c", "user.name=argus", "-c", "user.email=argus@example.invalid"]


@pytest.fixture(autouse=True)
def git_command() -> Iterator[None]:
    previous = conventions.override({"commands.git": "git"})
    yield
    conventions.configure(previous)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _commit(cwd: Path, name: str, text: str, message: str) -> None:
    (cwd / name).write_text(text, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)


def _no_repo(_root: str | Path | None = None) -> None:
    return None


def _point_board_at(monkeypatch: pytest.MonkeyPatch, board: Path) -> None:
    def board_only(_root: str | Path | None = None) -> Path:
        return board

    monkeypatch.setattr(sync, "board_repo", board_only)
    monkeypatch.setattr(sync, "phases_repo", _no_repo)
    monkeypatch.setattr(sync, "runstate_repo", _no_repo)


@pytest.fixture
def remotes(tmp_path: Path) -> dict[str, Path]:
    """A bare origin on main, a public clone on main, and a board clone.

    The board clone sits on a claims branch with its own commit. Origin main
    then moves on, so a merge of main into claims would succeed.
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _commit(seed, "base.txt", "one\n", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")

    public = tmp_path / "public"
    _git(tmp_path, "clone", "-q", str(origin), str(public))
    board = tmp_path / "board"
    _git(tmp_path, "clone", "-q", str(origin), str(board))
    _git(board, "checkout", "-q", "-b", "claims")
    _commit(board, "claim.txt", "claim\n", "claim a card")

    _commit(seed, "moved.txt", "two\n", "main moves on")
    _git(seed, "push", "-q", "origin", "main")
    return {"origin": origin, "public": public, "board": board}


def test_board_on_claims_branch_is_not_merged(
    remotes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    board = remotes["board"]
    before = _git(board, "rev-parse", "HEAD")
    _point_board_at(monkeypatch, board)

    result = sync.mode_resync(remotes["public"])

    board_entry = next(
        r for r in result["repos"] if Path(r["repo"]).resolve() == board.resolve()
    )
    assert board_entry["ok"] is True
    assert board_entry["action"] == "skipped-board-checkout"
    assert _git(board, "rev-parse", "HEAD") == before
    assert not (board / "moved.txt").exists()
    assert result["ok"] is True


def test_board_on_integration_branch_keeps_the_noop(
    remotes: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board_main = tmp_path / "board-main"
    _git(tmp_path, "clone", "-q", str(remotes["origin"]), str(board_main))
    _point_board_at(monkeypatch, board_main)

    result = sync.mode_resync(remotes["public"])

    entry = next(
        r for r in result["repos"] if Path(r["repo"]).resolve() == board_main.resolve()
    )
    assert entry["ok"] is True
    assert entry["action"] == "already-on-integration-branch"


def test_refused_merge_is_not_reported_as_continued(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _commit(seed, "base.txt", "one\n", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")

    phase = tmp_path / "phase"
    _git(tmp_path, "clone", "-q", str(origin), str(phase))
    _git(phase, "checkout", "-q", "--orphan", "feature/unrelated")
    _git(phase, "rm", "-rq", "--cached", ".")
    _commit(phase, "work.txt", "work\n", "unrelated root")

    result = sync.resync_integration(phase, base="main")

    assert result["ok"] is False
    assert result["action"] == "blocked"
