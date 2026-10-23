# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The hook setup installs only when the pre_run hook refuses, and backs up first."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from argus import hooks_setup

CHECK_SCRIPT = """\
import pathlib, sys
if pathlib.Path(".git/hooks/pre-commit").exists():
    sys.exit(0)
print("no hooks installed", file=sys.stderr)
sys.exit(1)
"""

INSTALL_SCRIPT = """\
import pathlib
hooks = pathlib.Path(".git/hooks")
hooks.mkdir(parents=True, exist_ok=True)
(hooks / "pre-commit").write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
"""


@pytest.fixture
def git_exe() -> str:
    found = shutil.which("git")
    assert found is not None
    return found


@pytest.fixture
def checkout(
    tmp_path: Path, git_exe: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "work"
    repo.mkdir()
    subprocess.run([git_exe, "init", "-q", str(repo)], check=True)
    check = tmp_path / "check_hooks.py"
    check.write_text(CHECK_SCRIPT, encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"hooks:\n  pre_run: {[sys.executable, str(check)]!r}\n", encoding="utf-8"
    )
    installer = tmp_path / "install_hooks.py"
    installer.write_text(INSTALL_SCRIPT, encoding="utf-8")
    run_dir = tmp_path / "state"
    run_dir.mkdir()
    return {
        "repo": repo,
        "pipeline": pipeline,
        "installer": installer,
        "run_dir": run_dir,
    }


def _install_argv(installer: Path) -> list[str]:
    return [sys.executable, str(installer)]


def test_present_hooks_change_nothing(checkout: dict[str, Path]) -> None:
    hooks = checkout["repo"] / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    status, line = hooks_setup.ensure_hooks(
        checkout["run_dir"],
        checkout["repo"],
        checkout["pipeline"],
        _install_argv(checkout["installer"]),
    )
    assert status == 0
    assert line.startswith("hooks: present")
    assert not (checkout["run_dir"] / "hooks-backup").exists()


def test_missing_hooks_are_installed_after_a_backup(checkout: dict[str, Path]) -> None:
    hooks = checkout["repo"] / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    status, line = hooks_setup.ensure_hooks(
        checkout["run_dir"],
        checkout["repo"],
        checkout["pipeline"],
        _install_argv(checkout["installer"]),
    )
    assert status == 0, line
    assert line.startswith("hooks: installed with")
    backups = list((checkout["run_dir"] / "hooks-backup").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "post-commit").exists()
    assert str(backups[0]) in line


def test_missing_hooks_without_the_command_is_one_needs_you(
    checkout: dict[str, Path],
) -> None:
    status, line = hooks_setup.ensure_hooks(
        checkout["run_dir"], checkout["repo"], checkout["pipeline"], None
    )
    assert status == 1
    assert line.startswith("Needs You.")
    assert "commands.hooks-install" in line
    assert "check: the pre_run hook line reads accepted" in line
    assert not (checkout["repo"] / ".git" / "hooks" / "pre-commit").exists()


def test_a_failing_install_command_is_named(checkout: dict[str, Path]) -> None:
    failing = checkout["repo"].parent / "fail_install.py"
    failing.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    status, line = hooks_setup.ensure_hooks(
        checkout["run_dir"],
        checkout["repo"],
        checkout["pipeline"],
        _install_argv(failing),
    )
    assert status == 1
    assert "exited 3" in line


def test_a_hook_that_still_refuses_after_install_is_named(
    checkout: dict[str, Path],
) -> None:
    noop = checkout["repo"].parent / "noop_install.py"
    noop.write_text("pass\n", encoding="utf-8")
    status, line = hooks_setup.ensure_hooks(
        checkout["run_dir"],
        checkout["repo"],
        checkout["pipeline"],
        _install_argv(noop),
    )
    assert status == 1
    assert "still refuses" in line
