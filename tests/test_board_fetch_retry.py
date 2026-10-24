# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A board fetch that loses a reference lock race is retried; a lost race fails closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.board import branch
from argus.board.core import CoordinationUnavailable

REF = "refs/remotes/origin/fixture-claims"
LOCK_ERROR = (
    "error: cannot lock ref 'refs/remotes/origin/fixture-claims': "
    "is at a but expected b"
)


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
        return 0, "", ""


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(branch.time, "sleep", lambda _seconds: None)


@pytest.mark.usefixtures("no_wait")
def test_a_lock_race_is_retried_until_the_fetch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = ScriptedGit([(1, "", LOCK_ERROR), (0, "", "")])
    monkeypatch.setattr(branch, "_git", git)
    found = branch._fetch_branch_ref(Path("board"), "origin", "fixture-claims")
    assert found == (REF, "c0ffee")
    assert git.fetch_calls == 2


@pytest.mark.usefixtures("no_wait")
def test_a_lock_race_that_keeps_failing_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = ScriptedGit([(1, "", LOCK_ERROR)] * 3)
    monkeypatch.setattr(branch, "_git", git)
    with pytest.raises(CoordinationUnavailable, match="coordination fetch failed"):
        branch._fetch_branch_ref(Path("board"), "origin", "fixture-claims")
    assert git.fetch_calls == 3


@pytest.mark.usefixtures("no_wait")
def test_another_fetch_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = ScriptedGit([(1, "", "fatal: unable to access the remote")])
    monkeypatch.setattr(branch, "_git", git)
    with pytest.raises(CoordinationUnavailable, match="coordination fetch failed"):
        branch._fetch_branch_ref(Path("board"), "origin", "fixture-claims")
    assert git.fetch_calls == 1
