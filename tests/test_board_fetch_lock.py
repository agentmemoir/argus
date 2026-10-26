# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Board fetches of one checkout take turns on one lock; a busy lock fails closed."""

from __future__ import annotations

import fcntl
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.board import branch, fetch_lock
from argus.board.core import CoordinationUnavailable

REF = "refs/remotes/origin/fixture-claims"


class ScriptedGit:
    """Answer each fetch from a script, say the branch exists, and resolve a commit."""

    def __init__(self, fetches: list[tuple[int, str, str]]) -> None:
        self.fetches = list(fetches)
        self.fetch_calls = 0

    def __call__(
        self, _private: object, *args: str, **_options: object
    ) -> tuple[int, str, str]:
        if args[:1] == ("fetch",):
            self.fetch_calls += 1
            if not self.fetches:
                raise AssertionError("more fetches than the script allows")
            return self.fetches.pop(0)
        if args[:2] == ("rev-parse", "--verify"):
            return 0, "c0ffee", ""
        if args[:1] == ("rev-parse",):
            return 0, ".git", ""
        return 0, "", ""


def _checkout(tmp_path: Path) -> Path:
    private = tmp_path / "board"
    (private / ".git").mkdir(parents=True)
    return private


def test_a_fetch_takes_the_checkout_lock_and_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = _checkout(tmp_path)
    git = ScriptedGit([(0, "", "")])
    monkeypatch.setattr(branch, "_git", git)
    monkeypatch.setattr(fetch_lock, "_git", git)
    found = branch._fetch_branch_ref(private, "origin", "fixture-claims")
    assert found == (REF, "c0ffee")
    assert git.fetch_calls == 1
    # The lock is released, so another handle can take it at once.
    with (private / ".git" / fetch_lock.FETCH_LOCK_NAME).open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_a_busy_lock_fails_closed_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = _checkout(tmp_path)
    git = ScriptedGit([(0, "", "")])
    monkeypatch.setattr(branch, "_git", git)
    monkeypatch.setattr(fetch_lock, "_git", git)
    monkeypatch.setattr(fetch_lock, "FETCH_TIMEOUT_S", 0.2)
    monkeypatch.setattr(fetch_lock, "FETCH_LOCK_POLL_S", 0.01)
    with (private / ".git" / fetch_lock.FETCH_LOCK_NAME).open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        with pytest.raises(CoordinationUnavailable, match="holds the board fetch lock"):
            branch._fetch_branch_ref(private, "origin", "fixture-claims")
    assert git.fetch_calls == 0


def test_a_no_wait_read_refuses_at_once_while_the_lock_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = _checkout(tmp_path)
    git = ScriptedGit([(0, "", "")])
    monkeypatch.setattr(branch, "_git", git)
    monkeypatch.setattr(fetch_lock, "_git", git)
    monkeypatch.setattr(fetch_lock, "FETCH_TIMEOUT_S", 30)
    monkeypatch.setattr(fetch_lock, "FETCH_LOCK_POLL_S", 0.01)
    with (private / ".git" / fetch_lock.FETCH_LOCK_NAME).open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(CoordinationUnavailable, match="holds the board fetch lock"):
            branch._fetch_branch_ref(private, "origin", "fixture-claims", wait=False)
        assert time.monotonic() - started < 5
    assert git.fetch_calls == 0


def test_a_failed_fetch_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = _checkout(tmp_path)
    git = ScriptedGit([(1, "", "fatal: unable to access the remote")])
    monkeypatch.setattr(branch, "_git", git)
    monkeypatch.setattr(fetch_lock, "_git", git)
    with pytest.raises(CoordinationUnavailable, match="coordination fetch failed"):
        branch._fetch_branch_ref(private, "origin", "fixture-claims")
    assert git.fetch_calls == 1


HOLDER = """
import fcntl, sys, time
with open(sys.argv[1], "a+") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(60)
"""


def test_a_holder_in_another_process_past_the_wait_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = _checkout(tmp_path)
    git = ScriptedGit([(0, "", "")])
    monkeypatch.setattr(branch, "_git", git)
    monkeypatch.setattr(fetch_lock, "_git", git)
    monkeypatch.setattr(fetch_lock, "FETCH_TIMEOUT_S", 0.5)
    monkeypatch.setattr(fetch_lock, "FETCH_LOCK_POLL_S", 0.01)
    lock_path = private / ".git" / fetch_lock.FETCH_LOCK_NAME
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with pytest.raises(CoordinationUnavailable, match="holds the board fetch lock"):
            branch._fetch_branch_ref(private, "origin", "fixture-claims")
        assert time.monotonic() - started < 10
    finally:
        holder.kill()
        holder.wait()
    assert git.fetch_calls == 0
