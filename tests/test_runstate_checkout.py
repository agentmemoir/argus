# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The run-state checkout: its setting, its remote, its cleanup, and the
checkout a phase's run directory actually lives in.

``runstate.repo`` names the run-state checkout directly and overrides the one
derived from ``driver.runs``. The receipt remote is resolved on that checkout,
and a phase whose run directory sits in a different repository than
``driver.runs`` still commits where the directory lives.
"""

import getpass
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.files import _runstate_remote  # pyright: ignore[reportPrivateUsage]
from argus.gitsync.git import git
from argus.gitsync.merge import sync_one
from argus.gitsync.mode_clean import mode_clean
from argus.gitsync.runstate import commit_leftover_run_state
from argus.gitsync.runstate import runstate_repo as derived_runstate_repo
from argus.gitsync.selftest_fixture import _init_remote

PHASE = 100761


def _configure(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> None:
    monkeypatch.setattr(conventions, "_settings", {"commands.git": "git", **settings})


def _none(*_args: object, **_kwargs: object) -> None:
    """Stand in for a fence that holds and for an absent run-state checkout."""


def _run_pathspec(*_args: object, **_kwargs: object) -> str:
    return f"runs/phase-{PHASE:06d}"


class _Board:
    """Stands in for the board; the fence is proven in test_driver_write_fences."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def assert_owner(self, reservation: dict[str, Any]) -> dict[str, Any]:
        return dict(reservation)


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
    (run_dir / "reservation.json").write_text(
        json.dumps(
            {
                "machine_id": "server-01",
                "reservation_id": "rsv-fixture",
                "generation": 1,
                "phase": f"{PHASE:06d}",
            }
        )
    )
    monkeypatch.setattr("argus.board.reservations.ReservationStore", _Board)

    result = mode_clean(public, PHASE)
    assert result["ok"], result
    assert result["repos"][0]["repo"] == str(board)
    assert result["repos"][0]["action"] == "committed-and-pushed"
    assert git(board, "status", "--porcelain")[1] == ""

    sweep = mode_clean(public)
    assert sweep["ok"], sweep
    assert sweep["summary"] == "clean sync ok: no leftover run state", sweep
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


def test_clean_phase_without_a_checkout_still_reads_as_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A no-work summary is a clean result, so it starts with the same ``ok``
    # head word ``summarize`` prints; anything else reads as a stop.
    plain = tmp_path / "plain"
    (plain / "runs").mkdir(parents=True)
    _configure(monkeypatch, **{"repo-root": plain, "driver.runs": plain / "runs"})
    monkeypatch.setattr("argus.gitsync.mode_clean.phase_fence", _none)
    monkeypatch.setattr("argus.gitsync.mode_clean._phase_run_pathspec", _run_pathspec)
    monkeypatch.setattr("argus.gitsync.mode_clean.runstate_repo", _none)
    result = mode_clean(plain, PHASE)
    assert result["ok"], result
    assert result["summary"] == "clean sync ok: no private checkout at None", result


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


def test_commit_failure_names_no_identity_without_the_host_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    remote, runstate = _init_remote(tmp_path, "runstate")
    # A checkout with no identity is the failure that makes git write the local
    # user and machine name into its stderr, so this is the one the warning must
    # report without copying it.
    for key in ("user.email", "user.name"):
        assert git(runstate, "config", "--unset", key)[0] == 0
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
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
    warning = result["repos"][0]["warning"]
    assert result["repos"][0]["action"] == "continued", result
    assert "git exit" in warning, result
    assert "git has no user identity" in warning, result
    assert "unable to auto-detect" not in warning, result
    for leaked in (getpass.getuser(), socket.gethostname()):
        assert leaked not in warning, result
    assert "@" not in warning, result
    assert git(remote, "log", "--oneline", "main")[1].count("\n") == 0


def test_fetch_failure_names_no_identity_without_the_host_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    _remote, runstate = _init_remote(tmp_path, "runstate")
    # A checkout with no identity is what makes git write the local user and
    # machine name as '<user>@<host>.(none)', so the remote helper prints that
    # shape and the stored detail must not carry it.
    for key in ("user.email", "user.name"):
        assert git(runstate, "config", "--unset", key)[0] == 0
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    leak = f"{getpass.getuser()}@{socket.gethostname()}"
    monkeypatch.setenv("GIT_SSH_COMMAND", f"sh -c 'echo {leak} >&2; exit 1'")
    url = "ssh://example/repo.git"
    assert git(runstate, "remote", "set-url", "origin", url)[0] == 0
    result = sync_one(runstate, "start")
    assert result["ok"] is True, result
    assert result["action"] == "continued", result
    assert "the next tick retries" in result["warning"], result
    detail = str(result["detail"])
    assert "git exit" in detail, result
    assert "unable to auto-detect" not in detail, result
    for leaked in (getpass.getuser(), socket.gethostname()):
        assert leaked not in detail, result
    assert "@" not in detail, result


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
