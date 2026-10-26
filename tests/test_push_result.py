# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A push result reports what the push did, from the branch's own status line."""

from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import git, push_moved
from argus.gitsync.group_publication import _push_one
from argus.gitsync.receipts import _published_head
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _configure_self_test,
    _init_remote,
)

BRANCH = "feature/group-cache-warm"


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The self-test configures the git command before it makes a remote; restored after each test.
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


def _clone(tmp_path: Path) -> Path:
    base = tmp_path / "push"
    base.mkdir()
    pub: Path = _init_remote(base, "push")[1]
    _configure_self_test(pub, pub / BOARD_REL)
    assert ensure_phase_branch(pub, BRANCH)["ok"]
    return pub


@pytest.mark.parametrize(
    ("porcelain", "expected"),
    [
        ("To ../b\n=\trefs/heads/main:refs/heads/main\t[up to date]\nDone", False),
        (" \trefs/heads/main:refs/heads/main\tabc..def", True),
        ("*\trefs/heads/main:refs/heads/main\t[new branch]", True),
        ("+\trefs/heads/main:refs/heads/main\tforced update", True),
        # With push.followTags the same command also pushes annotated tags; a
        # moved tag is not a moved branch.
        (
            "=\trefs/heads/main:refs/heads/main\t[up to date]\n"
            "*\trefs/tags/v1:refs/tags/v1\t[new tag]",
            False,
        ),
        ("*\trefs/tags/v1:refs/tags/v1\t[new tag]", False),
        (" \trefs/heads/other:refs/heads/other\tabc..def", False),
        (
            "=\trefs/heads/main:refs/heads/main\t[up to date]\n"
            " \trefs/heads/other:refs/heads/other\tabc..def",
            False,
        ),
        ("Everything up-to-date", False),
        ("", False),
    ],
)
def test_push_moved_reads_the_branchs_own_status_line(
    porcelain: str, expected: bool
) -> None:
    # A remote group runs one `git push` per remote, so each parsed output
    # carries one remote's refs; only the branch that was pushed decides.
    assert push_moved(porcelain, "main") is expected


def test_push_result_reports_a_moved_branch_and_an_up_to_date_one(
    tmp_path: Path,
) -> None:
    pub = _clone(tmp_path)
    # The clone's branch is already on its remote.
    up_to_date = _push_one(pub)
    assert up_to_date["ok"] is True
    assert up_to_date["action"] == "nothing-to-push"
    (pub / "fix.txt").write_text("the fix\n", encoding="utf-8")
    assert git(pub, "add", "-A")[0] == 0
    assert git(pub, "commit", "-qm", "the fix")[0] == 0
    moved = _push_one(pub)
    assert moved["ok"] is True
    assert moved["action"] == "pushed"
    assert moved["after"] == git(pub, "rev-parse", "HEAD")[1]


def test_published_head_takes_an_up_to_date_pushs_head_too() -> None:
    results = [
        {"repo": "/repo", "ok": True, "action": "up-to-date", "after": "sync-head"},
        {"repo": "/other", "ok": True, "action": "nothing-to-push", "after": "other"},
        {
            "repo": "/repo",
            "ok": True,
            "action": "nothing-to-push",
            "after": "pushed-head",
        },
    ]
    # An up-to-date push still leaves the branch carrying this head, so the
    # receipt must record it; a sync result for the same repo is not a push.
    assert _published_head(results, "/repo") == "pushed-head"
