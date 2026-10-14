# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The helper that finds a temporary directory this host will execute.

Source: ``src/argus/exec_tempdir.py``. The point of the module is the three-way
probe result, so the tests pin its two failures apart: a directory the kernel
refuses to run a file in is not the same answer as a probe that could not be run
at all. Reading one as the other turns a skip into a false result, which is the
bug this helper exists to prevent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from argus import exec_tempdir
from argus.exec_tempdir import can_execute, make_exec_dir, refuses_execution


def test_make_exec_dir_returns_a_directory_this_host_runs() -> None:
    """The helper finds somewhere to build a fixture, or this host cannot."""
    directory = make_exec_dir()
    if not directory:
        pytest.skip("this host has no directory that permits execution")
    try:
        assert can_execute(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_make_exec_dir_rejects_an_unusable_candidate(tmp_path: Path) -> None:
    """A candidate the probe rejects is removed, and the search ends empty."""
    tried: list[str] = []

    def reject(directory: str) -> bool:
        tried.append(directory)
        return False

    assert make_exec_dir(probe=reject, parents=(str(tmp_path),)) == ""
    assert tried
    assert list(tmp_path.iterdir()) == []


def test_make_exec_dir_takes_the_first_usable_candidate(tmp_path: Path) -> None:
    """The first candidate that runs wins, and later ones are not tried."""
    first = tmp_path / "first"
    first.mkdir()
    seen: list[str] = []

    def usable(directory: str) -> bool:
        seen.append(directory)
        return Path(directory).parent == first

    chosen = make_exec_dir(probe=usable, parents=(str(first), str(tmp_path)))
    assert Path(chosen).parent.resolve() == first.resolve()
    assert len(seen) == 1


def test_a_directory_that_cannot_be_probed_is_not_a_refusal(tmp_path: Path) -> None:
    """A probe that was never written says nothing about execution."""
    missing = tmp_path / "absent"
    assert can_execute(str(missing)) is False
    assert refuses_execution(str(missing)) is False


def test_a_probe_the_kernel_refuses_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a PermissionError from starting the probe counts as a refusal."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(exec_tempdir.subprocess, "run", refuse)
    assert can_execute(str(tmp_path)) is False
    assert refuses_execution(str(tmp_path)) is True


def test_a_probe_that_times_out_is_unprobed_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout is a failure to find out, not proof the mount refuses."""

    def time_out(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="probe", timeout=10)

    monkeypatch.setattr(exec_tempdir.subprocess, "run", time_out)
    assert can_execute(str(tmp_path)) is False
    assert refuses_execution(str(tmp_path)) is False
