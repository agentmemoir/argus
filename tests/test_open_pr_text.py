# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The title and body a caller wrote reach the create, or the call is refused.

A file that is missing or blank has no test of its own before this one: the two
refusals sat in an allowlist row, which is a statement about a check and not a
check. Each test reads the argv the stub saw, so "the text reached --title" is
measured rather than assumed.
"""

from pathlib import Path

import pytest

from argus.gitsync.git import ConfigError
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


def test_a_title_file_reaches_the_create(workspace: Workspace) -> None:
    title_file = workspace.transport.parent / "title.md"
    title_file.write_text("warm the cache on boot\n", encoding="utf-8")
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH, title_file=title_file)

    assert out["ok"] is True, out
    creates = _creates(workspace)
    assert len(creates) == 1, creates
    assert creates[0][creates[0].index("--title") + 1] == title_file.read_text()


def test_a_body_file_reaches_the_create(workspace: Workspace) -> None:
    body_file = workspace.transport.parent / "body.md"
    body_file.write_text("Cold reads are the cost.\n", encoding="utf-8")
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    creates = _creates(workspace)
    assert len(creates) == 1, creates
    assert creates[0][creates[0].index("--body") + 1] == body_file.read_text()


def test_a_missing_title_file_is_refused_before_gh(workspace: Workspace) -> None:
    missing = workspace.transport.parent / "absent.md"

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch=BRANCH, title_file=missing)

    assert "--title-file" in str(refused.value)
    assert str(missing) in str(refused.value)
    assert _creates(workspace) == []


def test_a_blank_body_file_is_refused_before_gh(workspace: Workspace) -> None:
    blank = workspace.transport.parent / "blank.md"
    blank.write_text("  \n\t\n", encoding="utf-8")

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch=BRANCH, body_file=blank)

    assert "--body-file" in str(refused.value)
    assert "empty" in str(refused.value)
    assert _creates(workspace) == []
