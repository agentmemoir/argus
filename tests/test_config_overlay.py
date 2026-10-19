# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Machine overlay replacement, one-level merge, path origins, and refusal rules."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from argus.config import cli as cfgmod
from config_fixture import BASE, write


def overlay_config(directory: Path, document: dict[str, Any], overlay: object) -> Path:
    document["machine"]["settings-file"] = "machines/worker.jsonc"
    folder = directory / "machines"
    folder.mkdir(exist_ok=True)
    (folder / "worker.jsonc").write_text(
        "// one machine\n" + json.dumps(overlay), encoding="utf-8"
    )
    return write(directory, json.dumps(document))


def test_overlay_scalar_and_list_replace_preserving_base_sources(
    tmp_path: Path,
) -> None:
    document = copy.deepcopy(BASE)
    document["driver"]["include"] = ["base-*.md", "more-*.md"]
    config = cfgmod.load(
        overlay_config(
            tmp_path,
            document,
            {"driver": {"include": ["overlay-*.md"]}, "repo-root": "overlay-project"},
        )
    )
    assert config.get("driver.include") == ["overlay-*.md"]
    assert config.source("driver.include") == "overlay"
    assert config.get("repo-root") == tmp_path / "machines/overlay-project"
    assert config.source("repo-root") == "overlay"
    assert config.source("pipeline") == "file"
    assert config.get("driver.exclude") == ["phase-100499.md"]
    assert config.get("machine.settings-file") == tmp_path / "machines/worker.jsonc"


def test_overlay_can_clear_a_base_list(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["driver"]["exclude"] = ["base-100499.md"]
    config = cfgmod.load(
        overlay_config(tmp_path, document, {"driver": {"exclude": []}})
    )
    assert config.get("driver.exclude") == []


def test_overlay_paths_use_the_file_that_supplied_each_value(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    path = overlay_config(
        tmp_path,
        document,
        {"repo-root": "overlay-project", "machine": {"id-file": "machine.id"}},
    )
    (tmp_path / "machines/machine.id").write_text("overlay-worker\n", encoding="utf-8")
    config = cfgmod.load(path)
    assert config.get("repo-root") == tmp_path / "machines/overlay-project"
    assert config.get("machine.id") == "overlay-worker"
    assert config.source("machine.id") == "overlay"
    assert config.get("pipeline") == tmp_path / "pipeline.yaml"


def test_flags_beat_overlay_and_resolve_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = copy.deepcopy(BASE)
    path = overlay_config(
        tmp_path,
        document,
        {
            "repo-root": "../overlay-project",
            "driver": {"include": ["overlay-*.md"], "provider": "phases"},
        },
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    config = cfgmod.load(
        path,
        {"repo-root": "flag-project", "phase-glob": ["flag-*.md"], "driver": "phase"},
    )
    assert config.get("repo-root") == cwd / "flag-project"
    assert config.get("driver.include") == ["flag-*.md"]
    assert config.get("driver.provider") == "phase"
    assert config.source("repo-root") == "flag"
    assert config.source("driver.include") == "flag"


@pytest.mark.parametrize(
    "overlay, key",
    [
        ({"driver": {"includee": ["x"]}}, "driver.includee"),
        ({"phases": {"directoryy": "x"}}, "phases.directoryy"),
        ({"typo": {}}, "typo"),
        ({"machine": {"settings-file": "other.json"}}, "machine.settings-file"),
    ],
)
def test_overlay_rejects_new_keys_and_settings_chains(
    tmp_path: Path, overlay: object, key: str
) -> None:
    path = overlay_config(tmp_path, copy.deepcopy(BASE), overlay)
    with pytest.raises(cfgmod.ConfigError, match=key):
        cfgmod.load(path)


def test_overlay_validation_runs_after_merge(tmp_path: Path) -> None:
    path = overlay_config(
        tmp_path, copy.deepcopy(BASE), {"driver": {"state": "bad/state"}}
    )
    with pytest.raises(cfgmod.ConfigError, match="driver.state"):
        cfgmod.load(path)


def test_overlay_source_and_settings_path_appear_in_report(tmp_path: Path) -> None:
    path = overlay_config(
        tmp_path, copy.deepcopy(BASE), {"driver": {"include": ["overlay-*.md"]}}
    )
    config = cfgmod.load(path)
    assert "driver.include\toverlay\toverlay-*.md" in cfgmod.report_lines(config)
    assert "ARGUS_SOURCE_PHASE_INCLUDE='overlay'" in cfgmod.shell_lines(config)
    assert (
        f"machine.settings-file\tfile\t{tmp_path / 'machines/worker.jsonc'}"
        in cfgmod.report_lines(config)
    )


def test_unreadable_overlay_names_file(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["machine"]["settings-file"] = "missing.json"
    path = write(tmp_path, json.dumps(document))
    with pytest.raises(cfgmod.ConfigError, match="missing.json"):
        cfgmod.load(path)


@pytest.mark.parametrize("overlay", [[], "scalar", 3])
def test_overlay_must_be_an_object(tmp_path: Path, overlay: object) -> None:
    path = overlay_config(tmp_path, copy.deepcopy(BASE), overlay)
    with pytest.raises(cfgmod.ConfigError, match="worker.jsonc.*JSON object"):
        cfgmod.load(path)


@pytest.mark.parametrize("content", [b"{broken", b"\xff"])
def test_bad_overlay_parse_and_encoding_are_typed_failures(
    tmp_path: Path, content: bytes
) -> None:
    path = overlay_config(tmp_path, copy.deepcopy(BASE), {})
    (tmp_path / "machines/worker.jsonc").write_bytes(content)
    with pytest.raises(cfgmod.ConfigError, match="worker.jsonc"):
        cfgmod.load(path)
