# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The peek-ci mode: one read of a pull request's checks, never a wait."""

from pathlib import Path

import pytest

from argus.gitsync.mode_peek_ci import mode_peek_ci
from gh_mode_support import (
    BRANCH,
    STUB_PEEK_CI,
    URL,
    Workspace,
    build_workspace,
    check_row,
    open_pull_request,
)


@pytest.fixture
def workspace(
    tmp_path: Path, exec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Workspace:
    return build_workspace(
        tmp_path,
        exec_dir,
        monkeypatch,
        stub=STUB_PEEK_CI,
        defaults={"list": [], "checks": [], "checks_exit": 0},
        branch=None,
    )


def test_every_check_passing_is_the_only_way_to_pass(workspace: Workspace) -> None:
    workspace.answer(
        list=open_pull_request(),
        checks=[
            check_row("build", "pass"),
            check_row("lint", "pass"),
            check_row("test", "pass"),
        ],
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    assert out["mode"] == "peek-ci"
    assert out["state"] == "passing"
    row = out["repos"][0]
    assert row["ok"] is True
    assert row["number"] == 11
    assert row["url"] == URL
    assert row["state"] == "passing"
    assert row["checks"] == {
        "pass": 3,
        "fail": 0,
        "pending": 0,
        "skipping": 0,
        "cancel": 0,
        "total": 3,
    }
    assert not any("--watch" in call for call in workspace.commands())


def test_one_failing_check_is_failing_and_not_passing(workspace: Workspace) -> None:
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "fail"), check_row("lint", "pass")],
        checks_exit=1,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "failing"
    row = out["repos"][0]
    assert row["ok"] is False
    assert row["checks"]["fail"] == 1
    assert row["checks"]["pass"] == 1
    assert row["checks"]["total"] == 2
    assert any("build" in reason for reason in row["blockers"])
    assert "build" in out["summary"]


def test_a_pending_check_is_pending_and_the_mode_does_not_wait(
    workspace: Workspace,
) -> None:
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "pending"), check_row("lint", "pass")],
        checks_exit=8,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "pending"
    assert out["repos"][0]["checks"]["pending"] == 1
    # One read, never a watch: gh was called once and no wait flag was passed.
    assert len(workspace.check_calls()) == 1
    assert not any("--watch" in call for call in workspace.commands())


def test_all_skipped_exits_zero_and_must_not_be_reported_as_passing(
    workspace: Workspace,
) -> None:
    # gh exits 0 when nothing failed and nothing is pending, which a skipped
    # check satisfies. Reading the exit code alone would call this a pass.
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "skipping"), check_row("lint", "skipping")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["skipping"] == 2
    assert "build" in out["summary"]
    assert "lint" in out["summary"]
    assert any("skipped" in reason for reason in out["repos"][0]["blockers"])


def test_a_cancelled_check_is_not_a_pass(workspace: Workspace) -> None:
    workspace.answer(
        list=open_pull_request(), checks=[check_row("build", "cancel")], checks_exit=0
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert any("build" in reason for reason in out["repos"][0]["blockers"])


def test_an_unrecognised_bucket_is_named_in_the_summary(workspace: Workspace) -> None:
    # gh may add a bucket this mode does not know. Such a row is counted in the
    # total, so a summary that names only the four known buckets leaves it
    # unexplained while still saying the pull request did not pass.
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "pass"), check_row("flaky-job", "neutral")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert "flaky-job" in out["summary"]
    assert "1 unknown bucket" in out["summary"]
    assert any("neutral" in reason for reason in out["repos"][0]["blockers"]), out[
        "repos"
    ][0]["blockers"]


def test_a_bucket_outside_the_known_set_is_not_a_pass(workspace: Workspace) -> None:
    # The whole dangerous path: every row is a non-pass, gh exits 0, and no
    # known bucket is non-zero. Reading only the known buckets calls this a pass.
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("flaky-job", "neutral")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["checks"]["pass"] == 0
    assert row["checks"]["total"] == 1
    assert any("does not know" in reason for reason in row["blockers"])


def test_gh_exiting_zero_without_a_check_list_is_not_a_pass(
    workspace: Workspace,
) -> None:
    # gh answered 0 but printed no list at all, so there are no rows to read.
    # A code of 0 with no rows is a fault, not an empty passing list.
    workspace.answer(list=open_pull_request(), checks_exit=0, checks=None)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    blockers = out["repos"][0]["blockers"]
    assert any("without a check list" in reason for reason in blockers), blockers


def test_a_non_zero_exit_beside_all_pass_rows_is_not_a_pass(
    workspace: Workspace,
) -> None:
    # The rows and the exit code disagree. Trusting the rows alone would report
    # a pass on an exit code gh uses to say something went wrong.
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "pass")],
        checks_exit=8,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["ok"] is False
    assert row["checks"]["pass"] == 1
    assert any(
        "gh exited 8 while every row said pass" in reason for reason in row["blockers"]
    ), row["blockers"]


def test_an_empty_check_list_is_not_a_pass(workspace: Workspace) -> None:
    workspace.answer(list=open_pull_request(), checks=[], checks_exit=0)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    assert any("no check rows" in reason for reason in out["repos"][0]["blockers"])


def test_no_checks_reported_at_all_is_not_a_pass(workspace: Workspace) -> None:
    # What gh really does on a branch with no checks: nothing on stdout, a
    # message on stderr, and a non-zero exit.
    workspace.answer(list=open_pull_request(), checks_exit=1, checks=None)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    blockers = out["repos"][0]["blockers"]
    assert any("could not report checks" in reason for reason in blockers)


def test_no_pull_request_for_the_branch_is_a_reason_not_a_crash(
    workspace: Workspace,
) -> None:
    workspace.answer(list=[])

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["number"] is None
    assert any(BRANCH in reason for reason in row["blockers"])
    assert workspace.check_calls() == []


def test_a_closed_pull_request_has_no_checks_in_flight(workspace: Workspace) -> None:
    workspace.answer(
        list=[
            {
                "number": 9,
                "state": "MERGED",
                "isDraft": False,
                "url": "https://github.com/example/argus/pull/9",
            }
        ]
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["repos"][0]["number"] == 9
    assert any("MERGED" in reason for reason in out["repos"][0]["blockers"])
    assert workspace.check_calls() == []
