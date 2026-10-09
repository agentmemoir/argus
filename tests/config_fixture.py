# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The sample, the base document, and the helpers shared by the config tests."""

import json
import subprocess
import sys
from pathlib import Path

from argus import config as cfgmod

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "config.sample.jsonc"

BASE = {
    "repo-root": "project",
    "pipeline": "pipeline.yaml",
    "roadmap-dir": "roadmap",
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


def check(condition, detail=""):
    if not condition:
        raise AssertionError(detail)


def write(directory, text, name="config.json"):
    # Every config here names machine.id-file, so the file it points at is
    # written beside it. The sample does the same for the tests that load it.
    (Path(directory) / "machine.id").write_text("machine-1\n", encoding="utf-8")
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


def sample_in(directory):
    """Copy the sample beside the machine-id file it names."""
    (Path(directory) / "machine.id").write_text("sample-machine\n", encoding="utf-8")
    path = Path(directory) / "config.json"
    path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def commented(document):
    text = json.dumps(document, indent=2)
    text = text.replace("{\n", "{\n  /* block comment */\n", 1)
    return "// leading line comment\n" + text + "\n// trailing comment\n"


def expect_error(call, needle):
    try:
        call()
    except cfgmod.ConfigError as exc:
        check(needle in str(exc), f"{needle!r} not in {str(exc)!r}")
        return
    raise AssertionError(f"no ConfigError naming {needle!r}")


def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "argus.config", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
    )
