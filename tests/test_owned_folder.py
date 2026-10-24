# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A folder Argus owns is made when it is missing, and the line names it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from argus import owned_folder

HELPER = Path(owned_folder.__file__).resolve()


def test_a_missing_folder_is_made_and_named(tmp_path: Path) -> None:
    folder = tmp_path / "runs" / "driver-state"
    line = owned_folder.ensure_owned_folder("driver state folder", folder)
    assert folder.is_dir()
    assert line == f"argus: created driver state folder {folder}"


def test_a_present_folder_adds_no_line(tmp_path: Path) -> None:
    assert owned_folder.ensure_owned_folder("run-state folder", tmp_path) == ""


def test_a_file_at_the_path_is_refused(tmp_path: Path) -> None:
    blocker = tmp_path / "runs"
    blocker.write_text("a file, not a folder\n", encoding="utf-8")
    with pytest.raises(OSError):
        owned_folder.ensure_owned_folder("run-state folder", blocker)


def test_the_driver_runs_it_as_a_script_and_gets_the_line(tmp_path: Path) -> None:
    folder = tmp_path / "runs"
    done = subprocess.run(
        [sys.executable, str(HELPER), "run-state folder", str(folder)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert folder.is_dir()
    assert done.stderr.strip() == f"argus: created run-state folder {folder}"


def test_a_refused_folder_exits_nonzero_with_a_cause(tmp_path: Path) -> None:
    blocker = tmp_path / "runs"
    blocker.write_text("a file\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(HELPER), "run-state folder", str(blocker)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1
    assert "is missing and cannot be created" in done.stderr
