# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`argus --check` runs the pipeline's pre_run hook through the real entry point."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFUSED = "pre_run hook: REFUSED (exit 1: no hooks installed)"
ACTION = "action: fix the pre_run hook so it exits 0, then run --check again"
ACCEPTED = "pre_run hook: accepted (exit 0)"
FAILING = [
    sys.executable,
    "-c",
    "import sys; print('no hooks installed', file=sys.stderr); sys.exit(1)",
]
PASSING = [sys.executable, "-c", "pass"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    )


def build(tmp_path: Path, stub: Path, hook: list[str] | None) -> tuple[Path, Path]:
    """Write a phase configuration whose pipeline declares the given pre_run hook."""
    root = tmp_path / "root"
    root.mkdir()
    git(root, "init", "-q")
    phases = tmp_path / "phases"
    (phases / "plans").mkdir(parents=True)
    (phases / "plans" / "phase-1.md").write_text("# phase\n", encoding="utf-8")
    git(phases, "init", "-q")
    git(phases, "add", "-A")
    git(phases, "commit", "-qm", "init")
    (tmp_path / "runs").mkdir()
    (tmp_path / "machine.id").write_text("fixture-machine\n", encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    hooks = "" if hook is None else f"hooks:\n  pre_run: {json.dumps(hook)}\n"
    pipeline.write_text(f"name: demo\n{hooks}", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "repo-root": str(root),
                "pipeline": str(pipeline),
                "number-input": "ticket",
                "file-input": "document",
                "phases": {
                    "repo": str(phases),
                    "directory": "plans",
                    "branch": "master",
                    "remote": "origin",
                },
                "driver": {
                    "provider": "phase",
                    "include": ["phase-*.md"],
                    "runs": "runs",
                    "state": "state",
                    "stale-after": 2700,
                    "outage-stale": 32400,
                    "quiet-minutes": 45,
                    "keep-logs-days": 7,
                    "merge-check-max": 5,
                    "recover-own-claim": False,
                },
                "machine": {"id-file": str(tmp_path / "machine.id")},
                "commands": {
                    "glide": str(stub),
                    "git": "git",
                    "tmux": "tmux",
                    "python": sys.executable,
                    "hermes": str(stub),
                    "gh": "gh",
                },
                "tmux": {
                    "session": "check-pre-run",
                    "width": 164,
                    "height": 56,
                    "idle-window": "idle",
                    "phase-window": "phase",
                    "idle-script": "phase-tmux-idle.sh",
                    "refresh": 15,
                },
            }
        ),
        encoding="utf-8",
    )
    return config, root


def check(config: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "bin/argus"), "--config", str(config), "--check"],
        cwd=root,
        env={**os.environ, "DISABLE_NOTIFY": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def stub(exec_dir: Path) -> Path:
    path = exec_dir / "glide"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_check_refuses_when_the_pre_run_hook_fails(
    tmp_path: Path, exec_dir: Path
) -> None:
    config, root = build(tmp_path, stub(exec_dir), FAILING)
    report = check(config, root)
    assert report.returncode != 0, report.stdout
    assert REFUSED in report.stdout, report.stdout
    assert ACTION in report.stdout, report.stdout


def test_check_reports_the_pre_run_hook_when_it_passes(
    tmp_path: Path, exec_dir: Path
) -> None:
    config, root = build(tmp_path, stub(exec_dir), PASSING)
    report = check(config, root)
    assert ACCEPTED in report.stdout, report.stdout
    assert "REFUSED" not in report.stdout, report.stdout
