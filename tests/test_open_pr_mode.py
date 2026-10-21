# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The open-pr mode: one draft for a branch, reused rather than duplicated."""

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
            # The checkout names no base branch of its own: it is on main,
            # and has no master and no origin/HEAD, so gh is asked.
            "repo_view": {"defaultBranchRef": {"name": "main"}},
        },
        branch=BRANCH,
    )


def test_creates_a_draft_and_reports_what_the_read_back_says(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    assert out["mode"] == "open-pr"
    # One vocabulary across modes: an upper-case state on the result, not only
    # on the row, so a record reader switches on the same key everywhere.
    assert out["state"] == "OPEN"
    row = out["repos"][0]
    assert row["action"] == "created"
    # These four come from the read after the create, not from the create.
    assert row["number"] == 11
    assert row["url"] == URL
    assert row["draft"] is True
    assert row["state"] == "OPEN"
    creates = [call for call in workspace.commands() if call[:2] == ["pr", "create"]]
    assert len(creates) == 1
    create = creates[0]
    assert "--draft" in create
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--title") + 1] == BRANCH
    reads = [call for call in workspace.commands() if call[:2] == ["pr", "list"]]
    assert len(reads) == 2, reads


def test_a_create_that_prints_no_url_is_blocked(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(list=[], create="")

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert row["action"] is None
    assert row["number"] is None
    assert row["url"] is None
    assert row["blockers"]
    assert any("no pull request URL" in reason for reason in row["blockers"])


def test_a_create_that_cannot_be_read_back_is_blocked(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(list=[], create=URL, reread=[])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert row["number"] is None
    assert any(URL in reason for reason in row["blockers"])


def test_a_failed_read_is_blocked_and_creates_nothing(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(list_exit=1)

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert any("could not list pull requests" in reason for reason in row["blockers"])
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_a_failed_create_is_blocked_and_claims_nothing(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(list=[], create=URL, create_exit=1)

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert row["action"] is None
    assert row["number"] is None
    assert row["url"] is None
    assert any("could not create" in reason for reason in row["blockers"])


def test_reuses_the_open_pull_request_and_creates_nothing(
    workspace: Workspace,
    body_file: Path,
) -> None:
    workspace.answer(
        list=[
            {"number": 7, "state": "OPEN", "isDraft": True, "url": "u/7"},
            {"number": 5, "state": "OPEN", "isDraft": False, "url": "u/5"},
        ]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["action"] == "reused"
    assert row["number"] == 7
    assert row["url"] == "u/7"
    assert row["draft"] is True
    assert row["state"] == "OPEN"
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_closed_or_merged_pull_request_blocks_a_duplicate(
    workspace: Workspace, body_file: Path, state: str
) -> None:
    workspace.answer(
        list=[{"number": 9, "state": state, "isDraft": False, "url": "u/9"}]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert row["number"] == 9
    assert row["state"] == state
    assert any("#9" in reason and state in reason for reason in row["blockers"])
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_a_phase_with_no_phases_file_is_refused(workspace: Workspace) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, phase=123456)
    assert "no phases file" in str(refused.value)
    assert workspace.commands() == []


def test_the_phase_file_names_the_branch(workspace: Workspace, body_file: Path) -> None:
    (workspace.phases / "phase-100440-backup-archive-format.md").write_text("# x\n")
    # The phase branch exists here: a phase whose branch was never created is
    # refused before gh, and that case has its own test below.
    assert git(workspace.root, "branch", "feature/backup-archive-format")[0] == 0
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, phase=100440, body_file=body_file)

    assert out["branch"] == "feature/backup-archive-format"
    assert out["ok"] is True, out


def test_a_phase_whose_branch_was_never_created_is_refused_by_name(
    workspace: Workspace,
) -> None:
    """The phase branch is checked against the checkout, as --branch already was.

    A phase branch that was never created reached gh, which returned a bare
    failure that named neither the branch nor the checkout, so a reader had
    nothing to act on.
    """
    (workspace.phases / "phase-100440-backup-archive-format.md").write_text("# x\n")

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, phase=100440)

    assert "feature/backup-archive-format" in str(refused.value)
    assert str(workspace.root) in str(refused.value)
    assert workspace.commands() == []


def test_an_explicit_branch_beats_the_phase_branch(
    workspace: Workspace, body_file: Path
) -> None:
    (workspace.phases / "phase-100440-backup-archive-format.md").write_text("# x\n")
    assert git(workspace.root, "branch", "feature/backup-archive-format")[0] == 0
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, phase=100440, branch=BRANCH, body_file=body_file)

    assert out["branch"] == BRANCH


def test_the_current_branch_is_used_when_neither_flag_is_given(
    workspace: Workspace,
    body_file: Path,
) -> None:
    assert git(workspace.root, "checkout", "-q", BRANCH)[0] == 0
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, body_file=body_file)

    assert out["branch"] == BRANCH
    assert out["ok"] is True, out


def test_a_detached_head_is_refused_by_name(workspace: Workspace) -> None:
    assert git(workspace.root, "checkout", "-q", "--detach", "HEAD")[0] == 0

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root)

    assert "detached HEAD" in str(refused.value)
    assert "has no commit yet" not in str(refused.value)
    assert workspace.commands() == []


@pytest.mark.usefixtures("workspace")
def test_a_checkout_with_no_commit_is_refused_by_name(tmp_path: Path) -> None:
    # The workspace fixture loads the git setting; the checkout under test is a
    # second, empty one.
    root = tmp_path / "fresh"
    root.mkdir()
    assert git(root, "init", "-q", "-b", "main")[0] == 0

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(root)

    assert "has no commit yet" in str(refused.value)
    assert "detached HEAD" not in str(refused.value)


def test_a_branch_this_checkout_lacks_is_refused_before_gh(
    workspace: Workspace,
) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch="feature/typo")

    assert "feature/typo" in str(refused.value)
    assert workspace.commands() == []


def test_a_pull_request_row_with_a_blank_url_is_not_reported(
    workspace: Workspace,
    body_file: Path,
) -> None:
    workspace.answer(
        list=[
            {"number": 8, "state": "OPEN", "isDraft": True, "url": ""},
            {"number": 7, "state": "OPEN", "isDraft": True},
            {"number": 6, "state": "OPEN", "isDraft": False, "url": "u/6"},
        ]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["action"] == "reused"
    assert row["number"] == 6
    assert row["url"] == "u/6"
