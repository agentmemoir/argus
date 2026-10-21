# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The open-pr command line: arguments, dispatch, and the exit code.

The mode's own answers live in ``test_open_pr_mode``. This file holds what the
command line adds: which checkout the flags name, what the record says, and the
code a caller gets back.
"""

import json
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import main
from argus.gitsync.git import ConfigError, git
from argus.gitsync.mode_open_pr import mode_open_pr
from gh_mode_support import (
    BRANCH,
    CREATED,
    STUB_OPEN_PR,
    URL,
    Workspace,
    build_workspace,
    keep_settings,
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


def test_the_cli_accepts_open_pr_and_reports_config_error(
    workspace: Workspace,
    body_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=[], create=URL, reread=[CREATED])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "open-pr",
            "--branch",
            BRANCH,
            "--body-file",
            str(body_file),
        ],
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "open-pr"
    assert out["repos"][0]["action"] == "created"

    workspace.calls.unlink()
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--phase", "123456"]
    )
    assert main.main() == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["state"] == "CONFIG_ERROR"
    assert "no phases file" in refused["error"]
    assert workspace.commands() == []


def test_open_pr_without_a_body_file_exits_two_with_a_config_error(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--branch", BRANCH]
    )

    assert main.main() == 2

    refused = json.loads(capsys.readouterr().out)
    assert refused["state"] == "CONFIG_ERROR"
    assert "--body-file" in refused["error"]
    creates = [call for call in workspace.commands() if call[:2] == ["pr", "create"]]
    assert creates == []


def test_a_create_the_cli_cannot_confirm_exits_one(
    workspace: Workspace,
    body_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=[], create="")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "open-pr",
            "--branch",
            BRANCH,
            "--body-file",
            str(body_file),
        ],
    )

    assert main.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["state"] == "BLOCKED"


def test_open_pr_honours_repo_and_names_that_checkout(
    workspace: Workspace,
    body_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--repo names the checkout acted on, and the record must say which one.

    The open-pr dispatch sits before the generic --repo branch, so a mode that
    reads args.root there acts on the configured root while the caller named
    another checkout, and opens a draft from the wrong one without an error.
    """
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    workspace.answer(list=[], create=URL, reread=[CREATED])
    other = workspace.root.parent / "other"
    other.mkdir()
    assert git(other, "init", "-q", "-b", "main")[0] == 0
    (other / "product.txt").write_text("work\n")
    assert git(other, "add", "-A")[0] == 0
    assert (
        git(
            other,
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-qm",
            "work",
        )[0]
        == 0
    )
    assert git(other, "branch", BRANCH)[0] == 0

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "open-pr",
            "--repo",
            str(other),
            "--branch",
            BRANCH,
            "--body-file",
            str(body_file),
        ],
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repos"][0]["repo"] == str(other.resolve())
    assert out["repos"][0]["repo"] != str(workspace.root.resolve())


def test_blocked_exits_one(
    workspace: Workspace,
    body_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace.answer(
        list=[{"number": 9, "state": "MERGED", "isDraft": False, "url": "u/9"}]
    )
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "open-pr",
            "--branch",
            BRANCH,
            "--body-file",
            str(body_file),
        ],
    )

    assert main.main() == 1


def test_the_help_names_the_new_modes_for_branch_and_phase(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flag's help must name every mode that reads it.

    Both strings said less than the code does: `--branch` named only start and
    push, and `--phase` did not name open-pr at all. A caller reading the help
    would not know the new modes accept them.
    """
    monkeypatch.setattr(sys, "argv", ["gitsync", "--help"])
    with pytest.raises(SystemExit):
        main.main()
    # argparse wraps the help at the terminal width, so the words are joined
    # before they are compared: the sentence, not the line breaks, is the claim.
    flat = " ".join(capsys.readouterr().out.split())

    assert "for the open-pr and peek-ci modes" in flat, flat
    assert "--mode open-pr or --mode peek-ci" in flat, flat


def _create_call(workspace: Workspace) -> list[str]:
    """The one `pr create` argv the stub recorded."""
    creates = [call for call in workspace.commands() if call[:2] == ["pr", "create"]]
    assert len(creates) == 1, creates
    return creates[0]


def test_an_explicit_base_is_used_and_nothing_is_asked_for_one(
    workspace: Workspace, body_file: Path
) -> None:
    """`--base` wins over every other source, so it is the way out of a stop."""
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file, base="trunk")

    assert out["ok"] is True, out
    create = _create_call(workspace)
    assert create[create.index("--base") + 1] == "trunk"
    # The fork question is a different one and is asked either way. What must not
    # happen is a second question about the base branch.
    asked = [
        call
        for call in workspace.commands()
        if call[:2] == ["repo", "view"] and "defaultBranchRef" in call
    ]
    assert asked == [], asked


def test_the_base_falls_back_to_gh_when_the_checkout_cannot_name_one(
    workspace: Workspace, body_file: Path
) -> None:
    """The checkout here has no master and no origin/HEAD, which is the fault.

    `integration_branch` answered `master` for this checkout whatever gh said,
    so a `main`-default repository got `--base master`. gh names the default
    branch instead, and that is the branch the create names.
    """
    workspace.answer(
        list=[],
        create=URL,
        reread=[CREATED],
        repo_view={"defaultBranchRef": {"name": "release"}},
    )

    out = mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert out["ok"] is True, out
    create = _create_call(workspace)
    assert create[create.index("--base") + 1] == "release"


def test_a_base_no_source_can_name_blocks_and_names_the_flag(
    workspace: Workspace, body_file: Path
) -> None:
    """A guess here is a bare gh exit on a real host, so the mode refuses first."""
    workspace.answer(list=[], create=URL, reread=[CREATED], repo_view_exit=1)

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch=BRANCH, body_file=body_file)

    assert "--base" in str(refused.value)
    assert BRANCH not in str(refused.value)
    creates = [call for call in workspace.commands() if call[:2] == ["pr", "create"]]
    assert creates == []
