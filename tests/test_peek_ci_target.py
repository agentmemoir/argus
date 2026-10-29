# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The peek-ci mode's pull request target: a number, a URL, or a branch."""

import json
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import main
from argus.gitsync.git import ConfigError, git
from argus.gitsync.mode_peek_ci import mode_peek_ci
from gh_mode_support import (
    BRANCH,
    STUB_PEEK_CI,
    URL,
    Workspace,
    build_workspace,
    check_row,
    keep_settings,
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


GITHUB_REMOTE = "https://github.com/example-org/example-repo.git"


def _publish_to_github_remote(root: Path) -> None:
    """Give the checkout a GitHub origin that its branch tracks, as a publish leaves it."""
    assert git(root, "remote", "add", "origin", GITHUB_REMOTE)[0] == 0
    assert git(root, "update-ref", "refs/remotes/origin/main", "HEAD")[0] == 0
    assert git(root, "branch", "--set-upstream-to=origin/main", "main")[0] == 0


def test_an_explicit_pull_request_still_reports_the_branch(
    workspace: Workspace,
) -> None:
    # The pull request comes from --pull-request, but the branch is still a fact
    # the reader needs, so it is resolved and reported rather than left null.
    workspace.answer(checks=[check_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    assert out["branch"] == "main"


def test_an_explicit_pull_request_survives_a_branch_that_cannot_be_resolved(
    workspace: Workspace,
) -> None:
    # A URL names the pull request on its own, so a checkout on a detached HEAD
    # is not a reason to refuse the read: the branch is null and the read holds.
    assert git(workspace.root, "checkout", "-q", "--detach")[0] == 0
    workspace.answer(checks=[check_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    assert out["branch"] is None


def test_an_explicit_url_is_read_directly(workspace: Workspace) -> None:
    workspace.answer(checks=[check_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["number"] == 11
    assert row["url"] == URL
    assert [call for call in workspace.commands() if call[:2] == ["pr", "list"]] == []
    checks = workspace.check_calls()
    assert len(checks) == 1
    assert URL in checks[0]
    assert checks[0][checks[0].index("--json") + 1] == "bucket,state,name,link"


def test_a_url_without_a_number_is_named_by_the_url(workspace: Workspace) -> None:
    # A URL ending in /files carries no pull request number. The blocker names
    # the URL instead of reading "for #None".
    files = f"{URL}/files"
    workspace.answer(checks_exit=1, checks=None)

    out = mode_peek_ci(workspace.root, pull_request=files)

    assert out["ok"] is False, out
    assert out["repos"][0]["number"] is None
    blockers = out["repos"][0]["blockers"]
    assert any(files in reason for reason in blockers), blockers
    assert not any("#None" in reason for reason in blockers), blockers


def test_an_explicit_number_keeps_the_url_unknown(workspace: Workspace) -> None:
    _publish_to_github_remote(workspace.root)
    workspace.answer(checks=[check_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request="42")

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["number"] == 42
    assert row["url"] is None
    assert row["remote"] == "origin"
    checks = workspace.check_calls()
    assert len(checks) == 1
    assert "42" in checks[0]
    assert checks[0][checks[0].index("--repo") + 1] == "example-org/example-repo"


def test_an_explicit_number_with_no_remote_is_refused_and_gh_is_not_run(
    workspace: Workspace,
) -> None:
    workspace.answer(checks=[check_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request="42")

    assert out["ok"] is False, out
    row = out["repos"][0]
    assert row["state"] == "UNKNOWN"
    assert row["remote"] is None
    assert any("--pull-request" in reason for reason in row["blockers"]), row
    assert workspace.check_calls() == []


def test_a_value_that_is_not_a_pull_request_is_refused_before_gh(
    workspace: Workspace,
) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_peek_ci(workspace.root, pull_request="not-a-pull-request")
    assert "not a pull request number or URL" in str(refused.value)
    assert workspace.commands() == []


def test_the_cli_accepts_the_peek_ci_mode(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=open_pull_request(), checks=[check_row("build", "pass")])

    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "peek-ci", "--branch", BRANCH]
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "peek-ci"
    assert out["ok"] is True
    assert out["repos"][0]["state"] == "PASSING"


def test_a_pending_pull_request_exits_one(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(
        list=open_pull_request(),
        checks=[check_row("build", "pending")],
        checks_exit=8,
    )

    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "peek-ci", "--branch", BRANCH]
    )
    assert main.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "PENDING"


def test_the_repo_flag_names_the_checkout_to_peek_at(
    workspace: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # peek-ci has its own dispatch branch, which comes before the generic --repo
    # branch. Without handling it there, --repo fell through to the repo root and
    # read the wrong checkout.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=open_pull_request(), checks=[check_row("build", "pass")])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "peek-ci",
            "--repo",
            str(other),
            "--branch",
            BRANCH,
        ],
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repos"][0]["repo"] == str(other.resolve())


def test_given_an_issue_run_when_peek_ci_runs_it_does_not_exit_config_error(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given an issue run: the checkout is on the issue branch, and that
    # branch's pull request has passing checks.
    branch = "issue/298-peek-ci-post-step"
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=open_pull_request(), checks=[check_row("build", "pass")])
    assert git(workspace.root, "checkout", "-q", "-b", branch)[0] == 0

    # When the issue post-step runs peek-ci with --kind issue and no branch flag.
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--kind", "issue", "--mode", "peek-ci"]
    )
    code = main.main()

    # Then the mode reads the issue branch's checks and the run is not refused.
    out = json.loads(capsys.readouterr().out)
    assert out["state"] != "CONFIG_ERROR", out
    assert code == 0, out
    assert out["branch"] == branch
    assert out["repos"][0]["state"] == "PASSING"
