# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two board fetches of one checkout, racing a writer, never hit git's ref lock."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from argus import conventions
from argus.board import branch
from argus.board.core import CoordinationUnavailable

BRANCH = "fixture-claims"
READERS = 2
READS = 60
PUSHES = 60
IDENTITY = ("-c", "user.email=coordination-test@example.com")
NAME = ("-c", "user.name=coordination test")


@pytest.fixture
def git_config() -> Iterator[None]:
    previous = conventions.configure({"commands.git": "git"})
    yield
    conventions.configure(previous)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(work: Path, text: str) -> None:
    (work / "state.txt").write_text(text, encoding="utf-8")
    _git(work, "add", "state.txt")
    _git(work, *IDENTITY, *NAME, "commit", "-q", "-m", text.strip())


def _push_states(work: Path) -> None:
    for number in range(PUSHES):
        _commit(work, f"{number}\n")
        _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")


def _reader(board: Path, outcomes: list[str]) -> None:
    for _ in range(READS):
        try:
            branch._fetch_branch_ref(board, "origin", BRANCH)
            outcomes.append("ok")
        except CoordinationUnavailable as exc:
            outcomes.append(str(exc))


@pytest.mark.usefixtures("git_config")
def test_concurrent_fetches_of_one_checkout_never_hit_the_ref_lock(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    _commit(work, "seed\n")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    board = tmp_path / "board"
    _git(tmp_path, "clone", "-q", str(remote), str(board))

    results: list[list[str]] = [[] for _ in range(READERS)]
    threads = [
        threading.Thread(target=_reader, args=(board, outcomes)) for outcomes in results
    ]
    threads.append(threading.Thread(target=_push_states, args=(work,)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    outcomes = [item for group in results for item in group]
    assert len(outcomes) == READERS * READS
    assert [item for item in outcomes if item != "ok"] == []
