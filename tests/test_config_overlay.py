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
from config_issue_fixture import issue_document


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
    document = issue_document()
    document["issues"]["trusted-authors"] = ["first", "second"]
    config = cfgmod.load(
        overlay_config(
            tmp_path,
            document,
            {"issues": {"preference": "hardest", "trusted-authors": ["worker"]}},
        )
    )
    assert config.get("issues.preference") == "hardest"
    assert config.source("issues.preference") == "overlay"
    assert config.get("issues.trusted-authors") == ["worker"]
    assert config.source("issues.trusted-authors") == "overlay"
    assert config.get("issues.difficulty-order") == [
        "difficulty:easy",
        "difficulty:hard",
    ]
    assert config.source("issues.difficulty-order") == "file"
    assert config.source("pipeline") == "file"
    assert config.source("issues.announce") == "default"
    assert config.get("machine.settings-file") == tmp_path / "machines/worker.jsonc"


def test_overlay_does_not_merge_deeper_than_one_level(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["comment-trust"] = {
        "authors": ["base"],
        "associations": ["OWNER"],
    }
    groups = {
        "difficulty": {
            "labels": ["difficulty:easy", "difficulty:hard"],
            "cardinality": "one",
        }
    }
    config = cfgmod.load(
        overlay_config(
            tmp_path,
            document,
            {
                "issues": {
                    "label-groups": groups,
                    "comment-trust": {"authors": ["overlay"]},
                }
            },
        )
    )
    assert config.get("issues.label-groups") == groups
    assert config.get("issues.comment-trust") == {"authors": ["overlay"]}
    assert config.source("issues.label-groups") == "overlay"


def test_overlay_can_clear_a_base_list(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["trusted-authors"] = ["base"]
    config = cfgmod.load(
        overlay_config(tmp_path, document, {"issues": {"trusted-authors": []}})
    )
    assert config.get("issues.trusted-authors") == []


def test_overlay_paths_use_the_file_that_supplied_each_value(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["codeowners-file"] = "OWNERS"
    document["machine"]["id-file"] = "machine.id"
    path = overlay_config(
        tmp_path,
        document,
        {
            "issues": {"task-dir": "../project/tasks", "codeowners-file": "OWNERS"},
            "machine": {"id-file": "machine.id"},
        },
    )
    (tmp_path / "machines/machine.id").write_text("overlay-worker\n", encoding="utf-8")
    config = cfgmod.load(path)
    assert config.get("issues.task-dir") == tmp_path / "project/tasks"
    assert config.get("issues.codeowners-file") == tmp_path / "machines/OWNERS"
    assert config.get("driver.runs") == tmp_path / "runs"
    assert config.get("machine.id") == "overlay-worker"
    assert config.source("machine.id") == "overlay"
    assert config.get("repo-root") == tmp_path / "project"


def test_flags_beat_overlay_and_resolve_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = copy.deepcopy(BASE)
    path = overlay_config(
        tmp_path,
        document,
        {
            "repo-root": "../overlay-project",
            "driver": {"include": ["overlay-*.md"], "provider": "roadmap"},
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
        ({"issues": {"preferance": "newest"}}, "issues.preferance"),
        ({"issues": {"category": "bug"}}, "issues.category"),
        ({"typo": {}}, "typo"),
        ({"machine": {"settings-file": "other.json"}}, "machine.settings-file"),
    ],
)
def test_overlay_rejects_new_keys_and_settings_chains(
    tmp_path: Path, overlay: object, key: str
) -> None:
    path = overlay_config(tmp_path, issue_document(), overlay)
    with pytest.raises(cfgmod.ConfigError, match=key):
        cfgmod.load(path)


def test_overlay_validation_runs_after_merge(tmp_path: Path) -> None:
    path = overlay_config(
        tmp_path, issue_document(), {"issues": {"preference": "unsupported"}}
    )
    with pytest.raises(cfgmod.ConfigError, match="issues.preference"):
        cfgmod.load(path)


def test_overlay_source_and_settings_path_appear_in_report(tmp_path: Path) -> None:
    path = overlay_config(
        tmp_path, issue_document(), {"issues": {"preference": "easiest"}}
    )
    config = cfgmod.load(path)
    assert "issues.preference\toverlay\teasiest" in cfgmod.report_lines(config)
    assert "ARGUS_SOURCE_ISSUES_PREFERENCE='overlay'" in cfgmod.shell_lines(config)
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
    path = overlay_config(tmp_path, issue_document(), overlay)
    with pytest.raises(cfgmod.ConfigError, match="worker.jsonc.*JSON object"):
        cfgmod.load(path)


@pytest.mark.parametrize("content", [b"{broken", b"\xff"])
def test_bad_overlay_parse_and_encoding_are_typed_failures(
    tmp_path: Path, content: bytes
) -> None:
    path = overlay_config(tmp_path, issue_document(), {})
    (tmp_path / "machines/worker.jsonc").write_bytes(content)
    with pytest.raises(cfgmod.ConfigError, match="worker.jsonc"):
        cfgmod.load(path)
