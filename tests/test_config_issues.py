# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue config: the removed provider, the trust block, and the schema surface."""

import copy
import json
from pathlib import Path

import pytest

from argus.config import cli as cfgmod
from config_fixture import BASE, write
from config_issue_fixture import issue_document


def test_issue_schema_matches_complete_key_set() -> None:
    expected = {
        "repo",
        "label-groups",
        "difficulty-order",
        "preference",
        "category",
        "search",
        "task-dir",
        "trusted-authors",
        "trusted-apps",
        "trusted-associations",
        "codeowners-file",
        "resolve-teams",
        "comment-trust",
        "untrusted-comments",
        "max-comments",
        "max-text-bytes",
        "list-limit",
        "pr-check-max",
        "timeout",
        "claim-attempts",
        "announce",
    }
    actual = {
        field.key.removeprefix("issues.")
        for field in cfgmod.SCHEMA
        if field.key.startswith("issues.")
    }
    assert actual == expected


def test_issue_provider_is_refused_at_start_time(tmp_path: Path) -> None:
    with pytest.raises(
        cfgmod.ConfigError, match="driver.provider.*known drivers: phase, phases"
    ):
        cfgmod.load(write(tmp_path, json.dumps(issue_document())))


@pytest.mark.parametrize("provider", ["phase", "phases"])
def test_phases_provider_rejects_issue_object(tmp_path: Path, provider: str) -> None:
    document = issue_document()
    document["driver"]["provider"] = provider
    with pytest.raises(cfgmod.ConfigError, match="contradicts.*driver.provider"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_phase_machine_loads_a_trust_only_issues_block(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["issues"] = {
        "trusted-associations": ["OWNER"],
        "untrusted-comments": "data-only",
    }
    config = cfgmod.load(write(tmp_path, json.dumps(document)))
    assert config.get("issues.trusted-associations") == ["OWNER"]
    assert config.get("issues.untrusted-comments") == "data-only"


def test_phase_machine_rejects_an_issue_source_key(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["issues"] = {"repo": "example/project"}
    with pytest.raises(cfgmod.ConfigError, match="contradicts.*driver.provider"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_phase_machine_rejects_a_bad_trust_value(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["issues"] = {"untrusted-comments": "show"}
    with pytest.raises(cfgmod.ConfigError, match="issues.untrusted-comments"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_a_null_trust_value_is_treated_as_absent(tmp_path: Path) -> None:
    document = copy.deepcopy(BASE)
    document["issues"] = {"trusted-authors": None}
    config = cfgmod.load(write(tmp_path, json.dumps(document)))
    assert config.get("issues.trusted-authors") is None
