# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""What open-pr refuses before it asks gh to create anything.

Three facts, and each one is a refusal with a way out: the title and body the
caller wrote, a fork checkout, and a branch the remote does not have. The tests
read the argv the stub saw, so "the text reached the call" is measured and not
assumed.
"""

from pathlib import Path

import pytest

from argus.gitsync.git import ConfigError, git
from argus.gitsync.mode_open_pr import mode_open_pr
from gh_mode_support import (
    BRANCH,
    CREATED,
    STUB_OPEN_PR,
    URL,
    Workspace,
    build_workspace,
)


@pytest.fixture
def workspace(
    tmp_path: Path, exec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Workspace:
    return build_workspace(
        tmp_path,
        exec_dir,
        monkeypatch,
        stub=STUB_OPEN_PR,
        defaults={
            "list": [],
            "create": URL,
            "reread": [CREATED],
            "repo_view": {"defaultBranchRef": {"name": "main"}, "isFork": False},
        },
        branch=BRANCH,
    )


def _creates(workspace: Workspace) -> list[list[str]]:
    return [call for call in workspace.commands() if call[:2] == ["pr", "create"]]


def test_a_fork_checkout_is_refused_by_name_with_a_way_out(
    workspace: Workspace,
) -> None:
    workspace.answer(
        repo_view={"defaultBranchRef": {"name": "main"}, "isFork": True},
        list=[],
        create=URL,
    )

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch=BRANCH)

    reason = str(refused.value)
    assert "fork" in reason
    # The way out, so a reader is not left with a rule and no next step.
    assert "gh pr create --repo" in reason
    assert _creates(workspace) == []


def test_a_branch_the_remote_never_saw_is_refused_by_name(
    workspace: Workspace,
) -> None:
    assert (
        git(workspace.root, "remote", "add", "origin", "https://example.test/r.git")[0]
        == 0
    )

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch=BRANCH)

    reason = str(refused.value)
    assert BRANCH in reason
    # The push the branch needs, named, so the refusal is not a dead end.
    assert f"git push -u origin {BRANCH}" in reason
    assert _creates(workspace) == []


def test_a_branch_the_remote_has_is_not_refused(workspace: Workspace) -> None:
    assert (
        git(workspace.root, "remote", "add", "origin", "https://example.test/r.git")[0]
        == 0
    )
    head = git(workspace.root, "rev-parse", f"refs/heads/{BRANCH}")[1].strip()
    assert head
    assert (
        git(workspace.root, "update-ref", f"refs/remotes/origin/{BRANCH}", head)[0] == 0
    )
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    assert len(_creates(workspace)) == 1
