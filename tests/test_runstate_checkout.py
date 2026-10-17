# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The run-state checkout: its setting, its remote, its cleanup, and the
checkout a phase's run directory actually lives in.

``runstate.repo`` names the run-state checkout directly and overrides the one
derived from ``driver.runs``. The receipt remote is resolved on that checkout,
and a phase whose run directory sits in a different repository than
``driver.runs`` still commits where the directory lives.
"""

from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.files import _runstate_remote  # pyright: ignore[reportPrivateUsage]
from argus.gitsync.git import git
from argus.gitsync.mode_clean import mode_clean
from argus.gitsync.runstate import commit_leftover_run_state
from argus.gitsync.runstate import runstate_repo as derived_runstate_repo
from argus.gitsync.selftest_fixture import _init_remote

PHASE = 100761


def _configure(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> None:
    monkeypatch.setattr(conventions, "_settings", {"commands.git": "git", **settings})


def test_runstate_repo_setting_overrides_the_derived_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _workflow_remote, workflow = _init_remote(tmp_path, "workflow")
    _runstate_remote, runstate = _init_remote(tmp_path, "runstate")
    # driver.runs is inside the workflow checkout, so the derived run-state
    # checkout is the workflow; the setting names a different one.
    _configure(
        monkeypatch,
        **{
            "repo-root": workflow,
            "driver.runs": workflow / "runs",
            "runstate.repo": runstate,
        },
    )
    assert conventions.runstate_repo(workflow) == runstate
    assert derived_runstate_repo(workflow, PHASE) == runstate


def test_mode_clean_commits_in_the_checkout_that_holds_the_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _public_remote, public = _init_remote(tmp_path, "public")
    _board_remote, board = _init_remote(tmp_path, "board")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"run_dir: {board}/runs/phase-{{phase_number:06d}}\n"
        "inputs:\n  phase_number: {type: int}\n  phase_file: {}\n"
    )
    # driver.runs is in the public checkout, but the pipeline puts the phase's
    # run directory in the board checkout.
    _configure(
        monkeypatch,
        **{
            "repo-root": public,
            "pipeline": pipeline,
            "phases.directory": board / "phases",
            "driver.runs": public / "runs",
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "number-input": "phase_number",
            "file-input": "phase_file",
            "board.repo": board,
            "board.branch": "main",
            "board.remote": "origin",
        },
    )
    run_dir = board / "runs" / f"phase-{PHASE:06d}"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text('{"state":"completed"}\n')

    result = mode_clean(public, PHASE)
    assert result["ok"], result
    assert result["repos"][0]["repo"] == str(board)
    assert result["repos"][0]["action"] == "committed-and-pushed"
    assert git(board, "status", "--porcelain")[1] == ""

    sweep = mode_clean(public)
    assert sweep["ok"], sweep
    assert "no leftover run state" in sweep["summary"], sweep
    assert git(board, "status", "--porcelain")[1] == ""


def test_runstate_remote_uses_the_only_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _remote, runstate = _init_remote(tmp_path, "runstate")
    # The checkout does not define the board's remote name and has just one,
    # so that one is the only safe choice.
    assert _runstate_remote(runstate, "board-upstream") == "origin"


def test_runstate_remote_uses_the_board_remote_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _remote, runstate = _init_remote(tmp_path, "runstate")
    assert git(runstate, "remote", "add", "board-upstream", str(tmp_path))[0] == 0
    assert _runstate_remote(runstate, "board-upstream") == "board-upstream"


def test_runstate_remote_refuses_two_remotes_without_a_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _remote, runstate = _init_remote(tmp_path, "runstate")
    assert git(runstate, "remote", "add", "second", str(tmp_path))[0] == 0
    _configure(monkeypatch)
    with pytest.raises(CoordinationUnavailable, match="set runstate.remote"):
        _runstate_remote(runstate, "board-upstream")


def test_runstate_remote_uses_a_configured_name_even_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _remote, runstate = _init_remote(tmp_path, "runstate")
    _configure(monkeypatch, **{"runstate.remote": "mirror"})
    assert _runstate_remote(runstate, "origin") == "mirror"


def test_commit_leftover_run_state_without_a_checkout_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "plain" / "runs"
    runs.mkdir(parents=True)
    _configure(
        monkeypatch,
        **{"repo-root": tmp_path / "plain", "driver.runs": runs},
    )
    result = commit_leftover_run_state(tmp_path / "plain", "close out")
    assert result["ok"] is False, result
    assert any(
        "no run-state checkout" in reason for reason in result["repos"][0]["blockers"]
    ), result


def test_commit_leftover_run_state_commits_in_a_valid_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    remote, runstate = _init_remote(tmp_path, "runstate")
    run_dir = runstate / "runs" / f"phase-{PHASE:06d}"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text('{"state":"completed"}\n')
    project = tmp_path / "project"
    project.mkdir()
    _configure(
        monkeypatch,
        **{"repo-root": project, "driver.runs": runstate / "runs"},
    )
    result = commit_leftover_run_state(project, "close out")
    assert result["ok"] is True, result
    assert result["repos"][0]["action"] == "committed-and-pushed", result
    assert (
        git(remote, "show", f"main:runs/phase-{PHASE:06d}/run.json")[1]
        == '{"state":"completed"}'
    )
