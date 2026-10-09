#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Exercise an installed source copy against an unrelated, local-only project.

The project's layout is deliberately unguessable: the roadmap sits at
``foo/bar/roadmap`` and the board in a separate checkout. Every value the tick
needs comes from one configuration file, so nothing here relies on a folder name
or on the caller's environment.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from argus.config import SHELL_VARS
from argus.exec_tempdir import exec_tempdir


ARGUS = Path(__file__).resolve().parents[1]
CONFIG_ENV_NAMES = frozenset(SHELL_VARS.values())


def run(command, cwd, env):
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=180
    )


def require(ok, message, detail=""):
    if not ok:
        raise AssertionError(f"{message}: {detail}")


def checkout(base, name, env):
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


def normalized(text):
    return re.sub(r"(?m)^\d{4}-\d{2}-\d{2}T\S+ ", "", text)


def write_config(path, *, project, board, roadmap, runs, pipeline, python):
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


def exercise(package, base):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CLIO_", "PHASE_", "DRIVER_", "ARGUS_"))
        and key not in CONFIG_ENV_NAMES
        and key not in {"WORK_SOURCE", "PYTHONPATH"}
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    )
    project = checkout(base, "project", env)
    board = checkout(base, "notes-checkout", env)
    roadmap = project / "foo/bar/roadmap"
    roadmap.mkdir(parents=True)
    item = roadmap / "phase-100010-first.md"
    item.write_text("# First\n- [ ] Work remains.\n")
    (roadmap / "index.md").write_text("Plan ready\n")
    runs = board / "runs"
    runs.mkdir()
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
    binary = base / "bin"
    result = run(["make", "install", f"BINDIR={binary}"], package, env)
    require(result.returncode == 0, "development install", result.stderr)
    # Keep the missing-command check independent of any host Glide install.
    for name in (
        "bash",
        "git",
        "tmux",
        "date",
        "sleep",
        "dirname",
        "basename",
        "readlink",
        "mkdir",
        "rmdir",
        "rm",
        "cat",
        "find",
        "sed",
        "grep",
        "awk",
        "sort",
        "tr",
        "cut",
        "head",
        "tail",
        "stat",
        "flock",
    ):
        source = shutil.which(name)
        if source:
            (binary / name).symlink_to(source)
    (binary / "python3").symlink_to(sys.executable)
    env.update(
        PATH=str(binary),
        GLIDE_CAPTURE=str(base / "glide-inputs.json"),
        FIXTURE_RUN_RESULT=str(board / "artifacts/item-100010/run.json"),
    )
    # A PATH command exercises the real launch boundary without starting a harness.
    fake_glide = binary / "glide"
    fake_glide.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['GLIDE_CAPTURE']).write_text(json.dumps(sys.argv[1:]))
result = Path(os.environ['FIXTURE_RUN_RESULT'])
result.parent.mkdir(parents=True, exist_ok=True)
result.write_text(json.dumps({'state': 'blocked'}))
sys.exit(2)
""")
    fake_glide.chmod(0o755)
    config = base / "argus-config.jsonc"
    write_config(
        config,
        project=project,
        board=board,
        roadmap=roadmap,
        runs=runs,
        pipeline=pipeline,
        python=sys.executable,
    )
    # The board migration is a direct tool call, so it loads the same file as
    # the driver.
    migrate = run(
        [
            sys.executable,
            str(package / "src/argus/next_phase.py"),
            "--config",
            str(config),
            "--migrate-board",
        ],
        base,
        env,
    )
    require(migrate.returncode == 0, "fixture board migration", migrate.stderr)

    # The readiness report names the file and every value's source. Nothing here
    # is read from the environment or derived from a folder name.
    readiness = run(["argus", "--config", str(config), "--check"], base, env)
    require(
        readiness.returncode == 0 and "MISSING:" not in readiness.stdout,
        "config readiness failed",
        readiness.stdout + readiness.stderr,
    )
    require(
        f"config file: {config}" in readiness.stdout,
        "the report did not name the configuration file",
        readiness.stdout,
    )
    for key in ("REPO", "PIPELINE", "ROADMAP_DIR", "WF", "BOARD_REPO"):
        require(
            re.search(rf"^{key}: .* \(file\)$", readiness.stdout, re.M),
            f"the report did not name {key} with its file source",
            readiness.stdout,
        )
    require(
        "private folder" not in readiness.stdout,
        "the report still described a derived private folder",
        readiness.stdout,
    )
    # A flag beats the file for one run, and the report says so.
    override = run(
        ["argus", "--config", str(config), "--repo-root", str(project), "--check"],
        base,
        env,
    )
    require(
        re.search(r"^REPO: .* \(flag\)$", override.stdout, re.M),
        "a flag override was not reported as a flag",
        override.stdout,
    )
    # With no flag, ./config.json is the file that is read.
    (base / "config.json").write_text(config.read_text())
    default_load = run(["argus", "--check"], base, env)
    require(
        default_load.returncode == 0,
        "the default ./config.json was not used",
        default_load.stdout + default_load.stderr,
    )
    require(
        f"config file: {base / 'config.json'}" in default_load.stdout,
        "the default config path was not named",
        default_load.stdout,
    )
    (base / "config.json").unlink()

    # Relative paths use the config file's folder even when Argus runs from a
    # different working directory. This catches a loader that anchors them on
    # the caller's current folder.
    elsewhere = project / "run-from-here"
    elsewhere.mkdir()
    relative_config = run(
        ["argus", "--config", str(config), "--dry-run"], elsewhere, env
    )
    require(
        relative_config.returncode == 0
        and "would reserve and launch phase 100010" in relative_config.stdout,
        "config paths resolve against the config file",
        relative_config.stdout + relative_config.stderr,
    )

    # Both entry points select the same work. The config names a relative folder
    # the layout cannot infer.
    commands = [
        ["bash", str(package / "src/argus/phase-driver.sh")],
        ["argus"],
    ]
    selections = []
    for command in commands:
        result = run([*command, "--config", str(config), "--dry-run"], base, env)
        require(result.returncode == 0, "entrypoint selection failed", result.stderr)
        selection = [
            line.split("dry-run: ", 1)[1]
            for line in result.stdout.splitlines()
            if "dry-run: " in line
        ]
        require(
            any("would reserve and launch phase 100010" in line for line in selection),
            "entrypoint selection changed",
            result.stdout + result.stderr,
        )
        selections.append(selection)
    require(selections[0] == selections[1], "entrypoint selection differs", selections)

    observations = []
    for command in commands:
        state = runs / "state"
        if state.exists():
            shutil.rmtree(state)
        result = run(
            [
                *command,
                "--config",
                str(config),
                "--session",
                "100010",
                str(item.relative_to(project)),
            ],
            base,
            env,
        )
        require(result.returncode == 2, "entrypoint session changed", result.stderr)
        halt = state / "halted"
        require(halt.is_file(), "entrypoint halt marker differs", result.stderr)
        marker = halt.read_text().split(" at ", 1)[0]
        require("rc=2 state=blocked" in marker, "pipeline run path was ignored", marker)
        log = normalized((state / "driver.log").read_text())
        messages = log.split("notify (disabled): ")[1:]
        require(len(messages) == 2, "entrypoint notification changed", log)
        observations.append((marker, messages))
        inputs = json.loads(Path(env["GLIDE_CAPTURE"]).read_text())
        require(
            "ticket=100010" in inputs
            and f"document={item.relative_to(project)}" in inputs,
            "pipeline input names were ignored",
            inputs,
        )
    require(
        observations[0] == observations[1],
        "entrypoint halt or notification differs",
        observations,
    )

    # Renamed items must take the same selection and notification paths. The
    # flag beats the file's include list for one run.
    renamed = item.with_name("work-100010-first.md")
    item.rename(renamed)
    shutil.rmtree(board / "artifacts")
    (runs / "state/halted").unlink()
    result = run(
        ["argus", "--config", str(config), "--phase-glob", "work-*.md", "--dry-run"],
        base,
        env,
    )
    require(
        "would reserve and launch phase 100010" in result.stdout,
        "a flag phase glob did not beat the file",
        result.stdout + result.stderr,
    )
    unknown = run(
        ["argus", "--config", str(config), "--driver", "unknown", "--dry-run"],
        base,
        env,
    )
    require(
        unknown.returncode == 2 and "known drivers: phase" in unknown.stderr,
        "unknown driver was accepted",
        unknown.stdout + unknown.stderr,
    )
    # Removing the configured command must stop a tick.
    fake_glide.unlink()
    result = run(["argus", "--config", str(config)], base, env)
    log = runs / "state/driver.log"
    evidence = result.stdout + result.stderr + (log.read_text() if log.exists() else "")
    require(
        result.returncode != 0 and "glide" in evidence,
        "missing glide did not stop the tick",
        evidence,
    )
    require(not (project / "scripts").exists(), "fixture acquired project scripts")


def main():
    try:
        with exec_tempdir(purpose="the installed Argus command") as scratch:
            base = Path(scratch)
            # Nothing beside this copy is a project tool or a vendored engine.
            package = base / "tools/source-checkout"
            shutil.copytree(
                ARGUS,
                package,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".git", ".venv", "dist", "target"
                ),
            )
            exercise(package, base)
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}")
        print("entrypoint self-test: FAIL")
        return 1
    print("entrypoint self-test: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
