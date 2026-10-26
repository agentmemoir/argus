# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The open-pr reads: what a gh answer must say before it is reported.

Each test here is one answer a reader could not have trusted: a row with no
usable fields, a row with no draft flag, and a failure code. The mode must name
the unknown and never fill it in with a default.
"""

from pathlib import Path

import pytest

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
        defaults={"list": [], "create": URL, "reread": [CREATED]},
        branch=BRANCH,
    )


def test_a_list_of_only_unreadable_rows_blocks_and_creates_nothing(
    workspace: Workspace,
    body_file: Path,
) -> None:
    # gh answered one row with no usable number, url, or state. Read as "no pull
    # request", that answer would open a duplicate draft for commits that
    # already have one, so it is a failed read and nothing is created.
    workspace.answer(list=[{"number": "eleven", "state": "OPEN", "url": ""}])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    blockers = out["repos"][0]["blockers"]
    assert any("gh listed 1 pull request rows" in reason for reason in blockers), (
        blockers
    )
    assert any(
        "none of them had a usable number, url, and state" in reason
        for reason in blockers
    ), blockers
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_a_row_with_no_draft_flag_blocks_and_reports_no_draft_state(
    workspace: Workspace,
    body_file: Path,
) -> None:
    workspace.answer(list=[{"number": 11, "state": "OPEN", "url": URL}])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    assert out["state"] == "BLOCKED"
    row = out["repos"][0]
    assert row["draft"] is None
    assert any("draft flag for #11" in reason for reason in row["blockers"]), row[
        "blockers"
    ]
    assert any("draft state is unknown" in reason for reason in row["blockers"])
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_the_string_false_is_not_a_draft_flag(
    workspace: Workspace, body_file: Path
) -> None:
    workspace.answer(
        list=[{"number": 11, "state": "OPEN", "isDraft": "false", "url": URL}]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is False, out
    row = out["repos"][0]
    assert row["draft"] is None
    assert row["draft"] is not True
    assert any("draft state is unknown" in reason for reason in row["blockers"])


def test_a_missing_credential_is_named_not_reported_as_a_number(
    workspace: Workspace,
    body_file: Path,
) -> None:
    workspace.answer(list_exit=4)

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    blockers = out["repos"][0]["blockers"]
    assert any(
        "could not authenticate (gh exited 4) for the list" in reason
        for reason in blockers
    ), blockers


def test_a_failed_create_names_the_step(workspace: Workspace, body_file: Path) -> None:
    workspace.answer(list=[], create=URL, create_exit=1)

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    blockers = out["repos"][0]["blockers"]
    assert any(
        "gh ran the create and failed (gh exited 1)" in reason for reason in blockers
    ), blockers


def test_the_summary_names_the_pull_request_action_and_draft_state(
    workspace: Workspace,
    body_file: Path,
) -> None:
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    row = out["repos"][0]
    assert row["number"] == 11 and row["action"] == "created" and row["draft"] is True
    assert "#11 created, draft" in out["summary"], out["summary"]


def test_a_fork_or_other_branch_pull_request_is_never_reused(
    workspace: Workspace,
    body_file: Path,
) -> None:
    # gh pr list --head matches the branch name only. A fork's pull request with
    # the same branch name, or a row for another head, is outsider text: open-pr
    # must not report it as this branch's pull request.
    fork = "https://github.com/example/argus/pull/12"
    other = "https://github.com/example/argus/pull/13"
    workspace.answer(
        list=[
            {
                "number": 12,
                "state": "OPEN",
                "isDraft": True,
                "url": fork,
                "isCrossRepository": True,
                "headRefName": BRANCH,
            },
            {
                "number": 13,
                "state": "MERGED",
                "isDraft": False,
                "url": other,
                "headRefName": "other/branch",
            },
        ],
        reread=[
            CREATED,
            {
                "number": 12,
                "state": "OPEN",
                "isDraft": True,
                "url": fork,
                "isCrossRepository": True,
                "headRefName": BRANCH,
            },
        ],
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["action"] == "created"
    assert row["url"] == URL
    assert row["number"] == 11
    assert len([c for c in workspace.commands() if c[:2] == ["pr", "create"]]) == 1
