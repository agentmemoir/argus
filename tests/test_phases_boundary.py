# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The separation proof refuses any two of the three repositories sharing a
destination, and allows three that differ."""

import subprocess
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.git import _assert_separate_destinations


def _repo(path: Path, remote: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(remote)], check=True
    )
    return path


def _configured() -> dict[str, object]:
    return conventions.configure({"commands.git": "git"})


def test_three_distinct_destinations_are_allowed(tmp_path: Path) -> None:
    previous = _configured()
    try:
        public = _repo(tmp_path / "public", tmp_path / "remotes" / "public.git")
        board = _repo(tmp_path / "board", tmp_path / "remotes" / "board.git")
        phases = _repo(tmp_path / "phases", tmp_path / "remotes" / "phases.git")
        _assert_separate_destinations(
            {"public": public, "board": board, "phases": phases}
        )
    finally:
        conventions.configure(previous)


def test_phases_sharing_the_board_destination_is_refused(tmp_path: Path) -> None:
    previous = _configured()
    try:
        shared = tmp_path / "remotes" / "shared.git"
        public = _repo(tmp_path / "public", tmp_path / "remotes" / "public.git")
        board = _repo(tmp_path / "board", shared)
        phases = _repo(tmp_path / "phases", shared)
        with pytest.raises(CoordinationUnavailable):
            _assert_separate_destinations(
                {"public": public, "board": board, "phases": phases}
            )
    finally:
        conventions.configure(previous)


def test_missing_remote_destinations_fail_closed(tmp_path: Path) -> None:
    previous = _configured()
    try:
        public = _repo(tmp_path / "public", tmp_path / "remotes" / "public.git")
        bare = tmp_path / "board"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(bare)], check=True)
        with pytest.raises(CoordinationUnavailable):
            _assert_separate_destinations({"public": public, "board": bare})
    finally:
        conventions.configure(previous)
