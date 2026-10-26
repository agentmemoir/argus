# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pre_run hook that `argus --check` runs before the first tick."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src/argus/prerun_hook.py"
ACTION = "action: fix the pre_run hook so it exits 0, then run --check again"


def pipeline(tmp_path: Path, pre_run: list[str] | None) -> Path:
    path = tmp_path / "pipeline.yaml"
    hooks = "" if pre_run is None else f"hooks:\n  pre_run: {pre_run!r}\n"
    path.write_text(f"name: demo\n{hooks}", encoding="utf-8")
    return path


def check(tmp_path: Path, path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), "--pipeline", str(path), *extra],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def failing() -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys; print('no hooks installed', file=sys.stderr); sys.exit(1)",
    ]


def test_failing_pre_run_hook_refuses_with_its_first_line(tmp_path: Path) -> None:
    report = check(tmp_path, pipeline(tmp_path, failing()))
    assert report.returncode != 0, report.stdout
    assert "pre_run hook: REFUSED (exit 1: no hooks installed)" in report.stdout
    assert ACTION in report.stdout


def test_passing_pre_run_hook_is_accepted(tmp_path: Path) -> None:
    ok = [sys.executable, "-c", "pass"]
    report = check(tmp_path, pipeline(tmp_path, ok))
    assert report.returncode == 0, report.stdout
    assert "pre_run hook: accepted (exit 0)" in report.stdout


def test_no_pre_run_declared_changes_nothing(tmp_path: Path) -> None:
    report = check(tmp_path, pipeline(tmp_path, None))
    assert report.returncode == 0, report.stdout
    assert report.stdout == ""


def test_host_override_replaces_the_pipeline_hook(tmp_path: Path) -> None:
    override = tmp_path / "hooks.toml"
    ok = [sys.executable, "-c", "pass"]
    override.write_text(f"[hooks]\npre_run = {ok!r}\n", encoding="utf-8")
    report = check(tmp_path, pipeline(tmp_path, failing()), "--override", str(override))
    assert report.returncode == 0, report.stdout
    assert "accepted" in report.stdout


def test_missing_hook_command_refuses(tmp_path: Path) -> None:
    absent = str(tmp_path / "absent-hook")
    report = check(tmp_path, pipeline(tmp_path, [absent]))
    assert report.returncode != 0, report.stdout
    assert "pre_run hook: REFUSED (could not start:" in report.stdout
    assert ACTION in report.stdout
