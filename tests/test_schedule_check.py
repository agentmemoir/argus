# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The schedule check reads the crontab and never edits it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus import schedule_check

ENTRY = "/opt/argus/bin/argus"


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["crontab", "-l"], returncode, stdout, stderr)


def test_an_active_line_for_this_config_is_present(tmp_path: Path) -> None:
    config = tmp_path / "argus.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    text = f"*/5 * * * * {ENTRY} --config {config}\n"
    assert schedule_check.runs_config(text, config)
    assert schedule_check.runs_config(f"*/5 * * * * x --config={config}\n", config)


def test_a_commented_line_or_another_config_does_not_count(tmp_path: Path) -> None:
    config = tmp_path / "argus.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    other = tmp_path / "other.jsonc"
    assert not schedule_check.runs_config(
        f"# */5 * * * * {ENTRY} --config {config}\n", config
    )
    assert not schedule_check.runs_config(
        f"*/5 * * * * {ENTRY} --config {other}\n", config
    )


def test_a_missing_line_gives_one_needs_you_with_the_line_and_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "argus.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(schedule_check, "read_crontab", lambda: ("none", ""))
    line = schedule_check.report(config, tmp_path / "state", ENTRY)
    assert line.startswith("Needs You. No crontab line runs this configuration")
    assert f"--config {config.resolve()}" in line
    assert "crontab -e" in line
    assert "crontab -l | grep -F" in line


def test_an_unreadable_crontab_is_unknown_not_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "argus.jsonc"
    config.write_text("{}\n", encoding="utf-8")

    def cannot_run(*_args: object, **_kwargs: object) -> None:
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", cannot_run)
    line = schedule_check.report(config, tmp_path / "state", ENTRY)
    assert line.startswith("schedule: unknown (crontab could not be run")


def test_no_crontab_for_the_user_reads_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_crontab(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return _completed(1, stderr="no crontab for user\n")

    monkeypatch.setattr(subprocess, "run", no_crontab)
    assert schedule_check.read_crontab() == ("none", "")


def test_the_exact_line_uses_flock_when_it_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "argus.jsonc"

    def flock_installed(_name: str) -> str:
        return "/usr/bin/flock"

    monkeypatch.setattr(schedule_check.shutil, "which", flock_installed)
    line = schedule_check.exact_line(config, tmp_path / "state", ENTRY)
    assert line == (
        f"*/5 * * * * flock -n {(tmp_path / 'state').resolve() / 'cron.lock'} "
        f"{ENTRY} --config {config.resolve()}"
    )


def test_a_run_without_a_configuration_prints_unknown_and_exits_zero() -> None:
    assert schedule_check.main(["schedule_check.py", "", "state", ENTRY]) == 0
