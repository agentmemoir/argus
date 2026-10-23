# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The stage temporary folder runs a file, or the check says why it cannot."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from argus import stage_tmpdir

HELPER = Path(stage_tmpdir.__file__).resolve()


def refuse(*_args: object, **_kwargs: object) -> None:
    """Stand in for a kernel that refuses to start the probe."""
    raise PermissionError(13, "Permission denied")


def cannot_run(*_args: object, **_kwargs: object) -> None:
    """Stand in for a probe that could not be started for another reason."""
    raise OSError(5, "Input/output error")


def test_a_folder_that_runs_a_file_is_created_and_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "stage-tmp"
    assert stage_tmpdir.main(["stage_tmpdir.py", str(folder)]) == 0
    assert folder.is_dir()
    assert "(runs)" in capsys.readouterr().out


def test_the_driver_runs_it_as_a_script(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(HELPER), str(tmp_path / "stage-tmp")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "(runs)" in done.stdout


def test_a_refused_folder_fails_and_names_the_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(subprocess, "run", refuse)
    assert stage_tmpdir.main(["stage_tmpdir.py", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "(refused)" in out
    assert "action: set driver.runs" in out


def test_a_probe_that_cannot_run_is_unprobed_not_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(subprocess, "run", cannot_run)
    assert stage_tmpdir.main(["stage_tmpdir.py", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "(unprobed)" in out
    assert "(refused)" not in out


def test_a_folder_that_cannot_be_created_is_unprobed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "stage-tmp"
    blocker.write_text("a file, not a folder\n", encoding="utf-8")
    assert stage_tmpdir.main(["stage_tmpdir.py", str(blocker)]) == 1
    out = capsys.readouterr().out
    assert "(unprobed)" in out
    assert "action: make the run-state folder writable" in out
