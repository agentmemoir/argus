# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run the installed command through the entrypoint scenario and assert the outcomes."""

import json
import os
import re
import shutil
import sys
from pathlib import Path

from entrypoint_fixture import (
    CONFIG_ENV_NAMES,
    checkout,
    require,
    run,
    write_config,
    write_pipelines,
)


def exercise(package: Path, base: Path) -> None:
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
    # The phases repository is a checkout of its own, nested in the project so
    # the driver may launch from it, and a distinct Git destination so the
    # separation proof holds. The project ignores it, as it would a submodule.
    phases_remote = base / "phases-checkout.git"
    result = run(
        ["git", "init", "--bare", "--initial-branch=master", str(phases_remote)],
        base,
        env,
    )
    require(result.returncode == 0, "fixture phases remote", result.stderr)
    phases_repo = project / "phases-checkout"
    for command in (
        ["git", "clone", str(phases_remote), str(phases_repo)],
        ["git", "-C", str(phases_repo), "config", "user.email", "test@example.com"],
        ["git", "-C", str(phases_repo), "config", "user.name", "Fixture"],
        ["git", "-C", str(phases_repo), "commit", "--allow-empty", "-m", "phases"],
        ["git", "-C", str(phases_repo), "push", "-u", "origin", "master"],
    ):
        result = run(command, base, env)
        require(result.returncode == 0, "fixture phases git command", result.stderr)
    (project / ".git/info/exclude").write_text("phases-checkout/\n", encoding="utf-8")
    phases = phases_repo / "foo/bar/phases"
    phases.mkdir(parents=True)
    item = phases / "phase-100010-first.md"
    item.write_text("# First\n- [ ] Work remains.\n")
    (phases / "index.md").write_text("Plan ready\n")
    runs = board / "runs"
    runs.mkdir()
    pipeline = write_pipelines(project, board)
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
        phases=phases,
        phases_repo=phases_repo,
        runs=runs,
        pipeline=pipeline,
        python=sys.executable,
    )
    # The board migration is a direct tool call, so it loads the same file as
    # the driver.
    migrate = run(
        [
            sys.executable,
            str(package / "src/argus/select/next_phase.py"),
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
    for key in (
        "REPO",
        "PIPELINE",
        "PHASES_REPO",
        "PHASES_DIRECTORY",
        "PHASES_BRANCH",
        "PHASES_REMOTE",
        "WF",
        "BOARD_REPO",
    ):
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

    # A relative flag path is relative to the caller's folder, not the file's.
    relative_flag = run(
        ["argus", "--config", str(config), "--repo-root", ".", "--check"],
        project,
        env,
    )
    require(
        re.search(
            rf"^REPO: {re.escape(str(project.resolve()))} \(flag\)$",
            relative_flag.stdout,
            re.M,
        ),
        "a relative --repo-root did not resolve against the working directory",
        relative_flag.stdout,
    )

    # Both entry points select the same work. The config names a relative folder
    # the layout cannot infer.
    commands = [
        ["bash", str(package / "src/argus/driver/phase-driver.sh")],
        ["argus"],
    ]
    selections: list[list[str]] = []
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

    residue_file = board / "artifacts/item-100010/failed.json"
    observations: list[dict[str, object]] = []
    for command in commands:
        state = runs / "state"
        if state.exists():
            shutil.rmtree(state)
        # Each session leaves its own record; drop the previous one first.
        residue_file.unlink(missing_ok=True)
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
        # Slice 1 retries a failed job, records the failure, and continues: the
        # session exits 0 and writes no halt marker.
        require(result.returncode == 0, "entrypoint session changed", result.stderr)
        require(residue_file.is_file(), "entrypoint session changed", result.stderr)
        residue = json.loads(residue_file.read_text())
        require(
            residue.get("phase") == "100010"
            and residue.get("state") == "blocked"
            and residue.get("rc") == 2,
            "the failed job was not recorded",
            residue,
        )
        # The wall-clock stamp differs between runs; compare the rest.
        residue.pop("failed_at", None)
        observations.append(residue)
        inputs = json.loads(Path(env["GLIDE_CAPTURE"]).read_text())
        require(
            "ticket=100010" in inputs
            and f"document={item.relative_to(project)}" in inputs,
            "pipeline input names were ignored",
            inputs,
        )
    require(
        observations[0] == observations[1],
        "entrypoint failure record differs",
        observations,
    )

    # Renamed items must take the same selection and notification paths. The
    # flag beats the file's include list; the dry run may name the held claim.
    renamed = item.with_name("work-100010-first.md")
    item.rename(renamed)
    shutil.rmtree(board / "artifacts")
    result = run(
        ["argus", "--config", str(config), "--phase-glob", "work-*.md", "--dry-run"],
        base,
        env,
    )
    require(
        "phase 100010" in result.stdout,
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
