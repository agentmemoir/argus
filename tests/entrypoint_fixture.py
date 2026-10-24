# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Build and describe the disposable project the entrypoint self-test runs against."""

import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from argus.config.schema import SHELL_VARS

ARGUS = Path(__file__).resolve().parents[1]
CONFIG_ENV_NAMES = frozenset(SHELL_VARS.values())


#: Wall time of each command the entrypoint test ran, as (seconds, label).
TIMINGS: list[tuple[float, str]] = []


def _label(command: list[str]) -> str:
    """Name a command for the log. A path shows only its last part."""
    parts = [Path(part).name if "/" in str(part) else str(part) for part in command]
    return " ".join(parts)[:80]


def run(
    command: list[str], cwd: str | Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=180
    )
    elapsed = time.monotonic() - started
    label = _label(command)
    TIMINGS.append((elapsed, label))
    print(f"entrypoint time: {elapsed:7.2f} s  {label}")
    return result


def print_slowest(count: int = 10) -> None:
    """Print the slowest commands of this run, slowest first."""
    print("entrypoint time: slowest 10 commands")
    for seconds, label in sorted(TIMINGS, reverse=True)[:count]:
        print(f"entrypoint time:   {seconds:7.2f} s  {label}")


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
    phases: Path,
    phases_repo: Path,
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
  "phases": {{
    "repo": "{phases_repo.relative_to(path.parent)}",
    "directory": "{phases.relative_to(phases_repo)}",
    "branch": "master",
    "remote": "origin"
  }},
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


def write_pipelines(project: Path, board: Path) -> Path:
    """Write the pipeline and the repair pipeline in project; return the pipeline."""
    pipeline = project / "pipeline.yaml"
    pipeline.write_text(
        """version: 1
run_dir: RUN_TEMPLATE
inputs:
  ticket: {type: int, required: true}
  document: {required: true}
start: build
ends: [completed, blocked]
steps:
  build: {stage: build.md, end: blocked}
""".replace("RUN_TEMPLATE", str(board / "artifacts/item-{ticket:06d}"))
    )
    # The repair pipeline sits beside the pipeline, so the readiness report names no
    # missing file. This exercise publishes no pull request, so no round reads it.
    (project / "repair.yaml").write_text("version: 1\n")
    return pipeline
