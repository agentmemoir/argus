# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sample, the base document, and the helpers shared by the config tests."""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import argus.config.cli as cfgmod

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "config.sample.jsonc"

PHASES_LOCAL = {"repo": ".", "directory": "plans", "branch": "main", "remote": "origin"}

BASE: dict[str, Any] = {
    "repo-root": "project",
    "pipeline": "pipeline.yaml",
    "phases": {
        "repo": "project",
        "directory": "phases",
        "branch": "main",
        "remote": "origin",
    },
    "number-input": "phase_number",
    "file-input": "phase_file",
    "driver": {
        "provider": "phase",
        "include": ["phase-*.md"],
        "exclude": ["phase-100499.md"],
        "runs": "runs",
        "state": "state",
        "stale-after": 2700,
        "outage-stale": 32400,
        "quiet-minutes": 45,
        "keep-logs-days": 7,
        "merge-check-max": 5,
        "recover-own-claim": False,
    },
    "machine": {"id-file": "machine.id"},
    "commands": {
        "glide": "glide",
        "git": "git",
        "tmux": "tmux",
        "python": "/usr/bin/python3",
        "hermes": "hermes",
        "gh": "gh",
    },
    "tmux": {
        "session": "development",
        "width": 164,
        "height": 56,
        "idle-window": "idle",
        "phase-window": "phase",
        "idle-script": "phase-tmux-idle.sh",
        "refresh": 15,
    },
}


def check(condition: object, detail: object = "") -> None:
    if not condition:
        raise AssertionError(detail)


def write(directory: str | Path, text: str, name: str = "config.json") -> Path:
    # Every config here names machine.id-file, so the file it points at is
    # written beside it. The sample does the same for the tests that load it.
    _work_tree(directory)
    (Path(directory) / "machine.id").write_text("machine-1\n", encoding="utf-8")
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


def sample_in(directory: str | Path) -> Path:
    """Copy the sample beside the machine-id file it names."""
    _work_tree(directory)
    (Path(directory) / "machine.id").write_text("sample-machine\n", encoding="utf-8")
    path = Path(directory) / "config.json"
    path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _work_tree(directory: str | Path) -> None:
    """Make the ``project`` and its nested ``phases`` real Git work trees.

    The loader refuses a ``phases.repo`` that is not a Git work tree. ``BASE``
    names ``project`` for phases; the sample names ``project/phases``, a
    distinct checkout nested in the project, so both are provided. Each gets a
    remote of its own, because a config that also names a board makes the
    board's own public/board separation proof run, and that proof needs both to
    carry a destination that differs.
    """
    for relative in (Path("project"), Path("project") / "phases"):
        repo = Path(directory) / relative
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").exists():
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "remote",
                    "add",
                    "origin",
                    str(repo) + ".git",
                ],
                check=True,
            )


def commented(document: object) -> str:
    text = json.dumps(document, indent=2)
    text = text.replace("{\n", "{\n  /* block comment */\n", 1)
    return "// leading line comment\n" + text + "\n// trailing comment\n"


def expect_error(call: Callable[[], object], needle: str) -> None:
    try:
        call()
    except cfgmod.ConfigError as exc:
        check(needle in str(exc), f"{needle!r} not in {str(exc)!r}")
        return
    raise AssertionError(f"no ConfigError naming {needle!r}")


def run_cli(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus.config.cli", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
    )
