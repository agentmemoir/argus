# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check the argus.config command line: formats, flags, and the default file."""

import json
import shlex
import tempfile
from pathlib import Path

from argus.config import cli as cfgmod
from config_fixture import BASE, check, run_cli, sample_in, write


def test_shell_format():
    with tempfile.TemporaryDirectory() as tmp:
        path = sample_in(tmp)
        base = Path(tmp).resolve()
        result = run_cli(["--config", str(path), "--format", "shell"], tmp)
        check(result.returncode == 0, result.stderr)
        out = result.stdout
        check(f"REPO={shlex.quote(str(base / 'project'))}" in out, out)
        check("PHASE_INCLUDE=( 'phase-*.md' )" in out, out)
        check("RECOVER_OWN_CLAIM=0" in out, out)
        check("NOTIFY_DISABLED=0" in out, out)
        check("PHASE_MACHINE_ID=sample-machine" in out, out)
        check("ARGUS_SOURCE_REPO='file'" in out, out)
        check("BOARD_REPO=" in out, out)


def test_shell_format_omits_absent_objects():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(BASE))
        result = run_cli(["--config", str(path), "--format", "shell"], tmp)
        check(result.returncode == 0, result.stderr)
        check("BOARD_REPO=" not in result.stdout, result.stdout)
        check("DISCORD_CHANNEL=" not in result.stdout, result.stdout)
        check("HERMES_PROFILE_NAME=" not in result.stdout, result.stdout)
        check("NOTIFY_DISABLED=" not in result.stdout, result.stdout)


def test_json_format():
    with tempfile.TemporaryDirectory() as tmp:
        path = sample_in(tmp)
        base = Path(tmp).resolve()
        result = run_cli(["--config", str(path), "--format", "json"], tmp)
        check(result.returncode == 0, result.stderr)
        data = json.loads(result.stdout)
        check(set(data) == {"path", "values", "sources"}, sorted(data))
        check(data["path"] == str(path.resolve()), data["path"])
        check(
            data["values"]["repo-root"] == str(base / "project"),
            data["values"]["repo-root"],
        )
        check(data["sources"]["driver.runs"] == "file", data["sources"])


def test_report_format():
    with tempfile.TemporaryDirectory() as tmp:
        path = sample_in(tmp)
        result = run_cli(["--config", str(path), "--format", "report"], tmp)
        check(result.returncode == 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line]
        present = cfgmod.load(path).values
        check({line.split("\t")[0] for line in lines} == set(present), lines)
        check(lines[0].split("\t")[:2] == ["repo-root", "file"], lines[0])
        check(all(len(line.split("\t")) == 3 for line in lines), lines)


def test_cli_flag_beats_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(BASE))
        other = (Path(tmp) / "other").resolve()
        result = run_cli(
            [
                "--config",
                str(path),
                "--repo-root",
                str(other),
                "--phase-glob",
                "work-*.md",
                "--driver",
                "roadmap",
                "--format",
                "shell",
            ],
            tmp,
        )
        check(result.returncode == 0, result.stderr)
        check("ARGUS_SOURCE_REPO='flag'" in result.stdout, result.stdout)
        check(f"REPO={shlex.quote(str(other))}" in result.stdout, result.stdout)
        check("DRIVER=roadmap" in result.stdout, result.stdout)
        check("PHASE_INCLUDE=( 'work-*.md' )" in result.stdout, result.stdout)


def test_default_config_is_used_when_present():
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, json.dumps(BASE))
        result = run_cli(["--format", "report"], tmp)
        check(result.returncode == 0, result.stderr)
        check(result.stdout.startswith("repo-root\tfile\t"), result.stdout)


def test_missing_default_config_fails_clearly():
    with tempfile.TemporaryDirectory() as tmp:
        result = run_cli(["--format", "report"], tmp)
        check(result.returncode == 2, (result.returncode, result.stdout))
        check("config.json" in result.stderr, result.stderr)
