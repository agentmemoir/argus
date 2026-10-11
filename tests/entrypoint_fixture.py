# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Build and describe the disposable project the entrypoint self-test runs against."""

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from argus.config.schema import SHELL_VARS

ARGUS = Path(__file__).resolve().parents[1]
CONFIG_ENV_NAMES = frozenset(SHELL_VARS.values())


def run(
    command: list[str], cwd: str | Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=180
    )


def require(ok: object, message: str, detail: object = "") -> None:
    if not ok:
        raise AssertionError(f"{message}: {detail}")


def checkout(base: Path, name: str, env: Mapping[str, str]) -> Path:
    remote, repo = base / f"{name}.git", base / name
    commands = [
        ["git", "init", "--bare", "--initial-branch=master", str(remote)],
        ["git", "clone", str(remote), str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        ["git", "-C", str(repo), "config", "user.name", "Fixture"],
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "fixture"],
        ["git", "-C", str(repo), "push", "-u", "origin", "master"],
    ]
    for command in commands:
        result = run(command, base, env)
        require(result.returncode == 0, "fixture git command", result.stderr)
    return repo


def normalized(text: str) -> str:
    return re.sub(r"(?m)^\d{4}-\d{2}-\d{2}T\S+ ", "", text)


def write_config(
    path: Path,
    *,
    project: Path,
    board: Path,
    roadmap: Path,
    runs: Path,
    pipeline: Path,
    python: str,
) -> None:
    """Write a hand-edited-style JSONC config. Relative paths resolve against it."""
    (path.parent / "machine-id").write_text("fixture-machine\n", encoding="utf-8")
    path.write_text(f"""// Argus configuration for the entrypoint fixture.
// Every relative path below resolves against the folder holding THIS file.
{{
  "repo-root": "{project.relative_to(path.parent)}",
  "pipeline": "{pipeline.relative_to(path.parent)}",
  "roadmap-dir": "{roadmap.relative_to(path.parent)}",
  "number-input": "ticket",
  "file-input": "document",

  "driver": {{
    "provider": "phase",
    "include": ["phase-*.md"],
    "runs": "{runs.relative_to(path.parent)}",
    "state": "state",
    "stale-after": 2700,
    "outage-stale": 32400,
    "quiet-minutes": 45,
    "keep-logs-days": 7,
    "merge-check-max": 5,
    "recover-own-claim": false
  }},

  "machine": {{ "id-file": "machine-id" }},

  "commands": {{
    "glide": "glide",
    "git": "git",
    "tmux": "tmux",
    "python": "{python}",
    "hermes": "hermes",
    "gh": "gh"
  }},

  "tmux": {{
    "session": "argus-equivalence-fixture",
    "width": 164,
    "height": 56,
    "idle-window": "idle",
    "phase-window": "phase",
    "idle-script": "phase-tmux-idle.sh",
    "refresh": 15
  }},

  "board": {{
    "repo": "{board.relative_to(path.parent)}",
    "file": "claims.json",
    "branch": "fixture-claims",
    "remote": "origin"
  }},

  "notify": {{
    "profile": "fixture-profile",
    "channel": "discord:000000000000000000",
    "disabled": true,
    "fallback": "",
    "heartbeat": ""
  }}
}}
""")
