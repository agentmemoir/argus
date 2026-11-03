# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every write to the board's tracking reference holds the checkout's fetch lock.

A board push moves the reference after the remote has the new commit. A fetch that
read the old value in that moment fails with "cannot lock ref", and the launch's claim
check then refuses a claim the board holds.
"""

from __future__ import annotations

import fcntl
import shutil
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board import branch, fetch_lock, filecommit, seed, worktree
from argus.board.git import _git
from argus.board.selftest_fixture import _git_test_repo
from argus.board.store import ReservationStore

BOARD = "lease-board"


def _lock_is_held(private: Path) -> bool:
    lock = private / ".git" / fetch_lock.FETCH_LOCK_NAME
    with lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


@pytest.fixture
def writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[tuple[str, bool]]]:
    """A real board checkout, and each write to its board reference with the lock."""
    git = shutil.which("git")
    assert git is not None
    root = tmp_path / "root"
    root.mkdir()
    private = tmp_path / "leases-client-1"
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": root,
            "board.repo": private,
            "board.branch": BOARD,
            "board.remote": "origin",
            "board.file": "coordination/phases.json",
            "commands.git": git,
        },
    )
    _git_test_repo(tmp_path, "leases")
    seen: list[tuple[str, bool]] = []

    def spying_git(repo: Any, *args: str, **options: Any) -> tuple[int, str, str]:
        if args[0] in {"push", "fetch"} and any(
            arg.endswith(f"refs/heads/{BOARD}") or f"refs/heads/{BOARD}:" in arg
            for arg in args
        ):
            seen.append((args[0], _lock_is_held(private)))
        return _git(repo, *args, **options)

    for module in (branch, filecommit, seed, worktree):
        monkeypatch.setattr(module, "_git", spying_git)
    return private, seen


def _assert_locked(seen: list[tuple[str, bool]], operation: str) -> None:
    assert operation in {name for name, _held in seen}
    assert [write for write in seen if not write[1]] == []


def test_a_claim_and_its_renewal_write_the_board_only_under_the_lock(
    writes: tuple[Path, list[tuple[str, bool]]],
) -> None:
    private, seen = writes
    store = ReservationStore(private.parent / "root")
    store.migrate_board()
    reservation = store.reserve("100010", "machine-one")
    seen.clear()
    store.renew(reservation)
    _assert_locked(seen, "push")


def test_seeding_the_board_writes_it_only_under_the_lock(
    writes: tuple[Path, list[tuple[str, bool]]],
) -> None:
    private, seen = writes
    ReservationStore(private.parent / "root").migrate_board()
    _assert_locked(seen, "push")


def test_a_file_transaction_writes_the_board_only_under_the_lock(
    writes: tuple[Path, list[tuple[str, bool]]],
) -> None:
    private, seen = writes
    ReservationStore(private.parent / "root").migrate_board()
    seen.clear()

    def write(path: Path) -> bool:
        (path / "note.txt").write_text("note\n")
        return True

    filecommit.file_transaction(
        private, "origin", BOARD, ["note.txt"], write, "add a note"
    )
    _assert_locked(seen, "push")
