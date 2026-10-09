#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check argus.config: JSONC, path resolution, flag precedence, and the CLI."""

import copy
import json
import sys
import tempfile
from pathlib import Path

import test_config_cli
from argus import config as cfgmod
from config_fixture import BASE, check, commented, expect_error, sample_in, write


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
    ("shell format", test_config_cli.test_shell_format),
    (
        "shell omits absent objects",
        test_config_cli.test_shell_format_omits_absent_objects,
    ),
    ("json format", test_config_cli.test_json_format),
    ("report format", test_config_cli.test_report_format),
    ("cli flag", test_config_cli.test_cli_flag_beats_file),
    ("default config", test_config_cli.test_default_config_is_used_when_present),
    (
        "missing default config",
        test_config_cli.test_missing_default_config_fails_clearly,
    ),
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
