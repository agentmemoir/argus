# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A push result reports what the push did, from the branch's own status line."""

import pytest

from argus.gitsync.git import push_moved


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
