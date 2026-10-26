# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`argus --check` refuses a git older than 2.38 with one line that names both versions."""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from test_check_pre_run import PASSING, build, stub
from test_check_pre_run import check as run_check

NEEDS = "2.38 or newer is needed for merge-tree --write-tree"


def _report(
    tmp_path: Path, exec_dir: Path, version: str
) -> subprocess.CompletedProcess[str]:
    config, root = build(tmp_path, stub(exec_dir), PASSING)
    # The config loader also runs the configured git, so every other call goes
    # to the real git. Only --version answers with the version under test.
    real_git = shutil.which("git")
    assert real_git is not None
    fake = exec_dir / f"git-{version}"
    fake.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = --version ]; then echo 'git version {version}'; exit 0; fi\n"
        f'exec {shlex.quote(real_git)} "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["commands"]["git"] = str(fake)
    config.write_text(json.dumps(data), encoding="utf-8")
    return run_check(config, root)


def _git_lines(report: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line for line in report.stdout.splitlines() if line.startswith("git version:")
    ]


def test_check_refuses_an_old_git_with_one_line(tmp_path: Path, exec_dir: Path) -> None:
    report = _report(tmp_path, exec_dir, "2.37.1")
    assert report.returncode != 0, report.stdout
    assert _git_lines(report) == [f"git version: REFUSED (found 2.37.1; {NEEDS})"], (
        report.stdout + report.stderr
    )


def test_check_accepts_git_2_38_or_newer(tmp_path: Path, exec_dir: Path) -> None:
    report = _report(tmp_path, exec_dir, "2.55.0")
    assert _git_lines(report) == ["git version: accepted (2.55.0)"], (
        report.stdout + report.stderr
    )
