#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check argus.config: JSONC, path resolution, flag precedence, and the CLI."""

import copy
import json
import shlex
import subprocess
import sys
import tempfile
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


def test_comments_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, commented(BASE))
        config = cfgmod.load(str(path))
        check(config.get("machine.id") == "machine-1", config.values)


def test_comment_markers_inside_strings_are_kept():
    document = copy.deepcopy(BASE)
    document["commands"]["gh"] = 'a//b/*c*/d quote" slash'
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(document))
        config = cfgmod.load(str(path))
        check(
            config.get("commands.gh") == 'a//b/*c*/d quote" slash',
            config.get("commands.gh"),
        )


def test_relative_path_resolves_against_config_dir():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(BASE))
        config = cfgmod.load(str(path))
        base = Path(tmp).resolve()
        check(config.get("repo-root") == base / "project", config.get("repo-root"))
        check(config.get("pipeline") == base / "pipeline.yaml", config.get("pipeline"))
        check(config.get("driver.runs") == base / "runs", config.get("driver.runs"))
        check(config.path == path.resolve(), config.path)


def test_absolute_path_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        absolute = (Path(tmp) / "outside").resolve()
        document = copy.deepcopy(BASE)
        document["repo-root"] = str(absolute)
        path = write(tmp, json.dumps(document))
        config = cfgmod.load(str(path))
        check(config.get("repo-root") == absolute, config.get("repo-root"))


def test_flag_overrides_file_and_records_source():
    other = Path(tempfile.gettempdir()).resolve() / "argus-elsewhere"
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(BASE))
        config = cfgmod.load(
            str(path),
            {
                "repo-root": str(other),
                "roadmap-dir": str(other),
                "phase-glob": "work-*.md",
                "driver": "roadmap",
            },
        )
        check(config.get("repo-root") == other, config.get("repo-root"))
        check(config.source("repo-root") == "flag", config.source("repo-root"))
        check(config.get("roadmap-dir") == other, config.get("roadmap-dir"))
        check(config.source("roadmap-dir") == "flag", config.source("roadmap-dir"))
        check(config.get("driver.provider") == "roadmap", config.get("driver.provider"))
        check(
            config.source("driver.provider") == "flag", config.source("driver.provider")
        )
        check(
            config.get("driver.include") == ["work-*.md"], config.get("driver.include")
        )
        check(
            config.source("driver.include") == "flag", config.source("driver.include")
        )
        check(config.source("driver.runs") == "file", config.source("driver.runs"))


def test_missing_required_key_names_it():
    with tempfile.TemporaryDirectory() as tmp:
        document = copy.deepcopy(BASE)
        del document["driver"]["runs"]
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.runs")
        document = copy.deepcopy(BASE)
        del document["commands"]["glide"]
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "commands.glide")


def test_wrong_type_names_key():
    with tempfile.TemporaryDirectory() as tmp:
        document = copy.deepcopy(BASE)
        document["driver"]["stale-after"] = "soon"
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.stale-after")

        document = copy.deepcopy(BASE)
        document["driver"]["stale-after"] = True
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.stale-after")

        document = copy.deepcopy(BASE)
        document["driver"]["recover-own-claim"] = 1
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.recover-own-claim")

        document = copy.deepcopy(BASE)
        document["driver"]["include"] = "phase-*.md"
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.include")

        document = copy.deepcopy(BASE)
        document["driver"]["include"] = [1]
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.include")


def test_bad_values_name_key():
    with tempfile.TemporaryDirectory() as tmp:
        document = copy.deepcopy(BASE)
        document["driver"]["state"] = "a/b"
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.state")

        document = copy.deepcopy(BASE)
        document["driver"]["include"] = []
        path = write(tmp, json.dumps(document))
        expect_error(lambda: cfgmod.load(str(path)), "driver.include")


def test_sample_loads_with_every_schema_key():
    with tempfile.TemporaryDirectory() as tmp:
        path = sample_in(tmp)
        base = Path(tmp).resolve()
        config = cfgmod.load(str(path))
        for field in cfgmod.SCHEMA:
            key = cfgmod._REPORT_KEY.get(field.key, field.key)
            check(key in config.values, f"sample missing {key}")
        check(config.get("repo-root") == base / "project", config.get("repo-root"))
        check(config.get("roadmap-dir") == base / "plans", config.get("roadmap-dir"))
        check(config.get("board.repo") == base, config.get("board.repo"))
        check(config.get("board.file") == "claims.json", config.get("board.file"))
        check(
            config.get("driver.recover-own-claim") is False,
            config.get("driver.recover-own-claim"),
        )
        check(config.get("notify.disabled") is False, config.get("notify.disabled"))
        check(config.get("machine.id") == "sample-machine", config.get("machine.id"))
        check(
            all(source == "file" for source in config.sources.values()), config.sources
        )


def test_machine_id_is_read_from_a_file():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = write(tmp, json.dumps(BASE))
        config = cfgmod.load(str(path))
        check(config.get("machine.id") == "machine-1", config.get("machine.id"))
        check(config.source("machine.id") == "file", config.source("machine.id"))
        # The first non-empty line wins; the rest is ignored.
        (base / "machine.id").write_text("\n  macbook \nignored\n", encoding="utf-8")
        config = cfgmod.load(str(path))
        check(config.get("machine.id") == "macbook", config.get("machine.id"))
        # A missing file names it.
        (base / "machine.id").unlink()
        expect_error(lambda: cfgmod.load(str(path)), "machine id file")
        # An empty file names it.
        (base / "machine.id").write_text("\n\n", encoding="utf-8")
        expect_error(lambda: cfgmod.load(str(path)), "machine id file")
        # A missing key names the key.
        document = copy.deepcopy(BASE)
        del document["machine"]
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        expect_error(lambda: cfgmod.load(str(path)), "machine.id-file")


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
        check(len(lines) == len(cfgmod.SCHEMA), (len(lines), len(cfgmod.SCHEMA)))
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


TESTS = (
    ("comments ignored", test_comments_are_ignored),
    ("comment markers in strings kept", test_comment_markers_inside_strings_are_kept),
    ("machine id from file", test_machine_id_is_read_from_a_file),
    ("relative path", test_relative_path_resolves_against_config_dir),
    ("absolute path", test_absolute_path_is_kept),
    ("flag override", test_flag_overrides_file_and_records_source),
    ("missing required key", test_missing_required_key_names_it),
    ("wrong type", test_wrong_type_names_key),
    ("bad values", test_bad_values_name_key),
    ("sample", test_sample_loads_with_every_schema_key),
    ("shell format", test_shell_format),
    ("shell omits absent objects", test_shell_format_omits_absent_objects),
    ("json format", test_json_format),
    ("report format", test_report_format),
    ("cli flag", test_cli_flag_beats_file),
    ("default config", test_default_config_is_used_when_present),
    ("missing default config", test_missing_default_config_fails_clearly),
)


def main():
    for name, test in TESTS:
        try:
            test()
        except AssertionError as exc:
            print(f"FAIL {name}: {exc}")
            return 1
    print("test_config: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
