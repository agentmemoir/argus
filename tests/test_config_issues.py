# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue-source config requirements, trust defaults, labels, and board boundaries."""

import copy
import json
from pathlib import Path
from typing import Any

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
        "timeout",
        "claim-attempts",
        "claim-label",
    }
    actual = {
        field.key.removeprefix("issues.")
        for field in cfgmod.SCHEMA
        if field.key.startswith("issues.")
    }
    assert actual == expected


def test_issue_optional_defaults_are_visible_only_on_issue_machines(
    tmp_path: Path,
) -> None:
    config = cfgmod.load(write(tmp_path, json.dumps(issue_document())))
    assert config.get("issues.trusted-associations") == ["OWNER", "MEMBER"]
    assert config.get("issues.trusted-authors") == []
    assert config.get("issues.trusted-apps") == []
    assert config.get("issues.resolve-teams") is False
    assert config.get("issues.untrusted-comments") == "ignore"
    assert config.get("issues.max-comments") == 50
    assert config.get("issues.max-text-bytes") == 60000
    assert config.get("issues.list-limit") == 200
    assert config.get("issues.timeout") == 30
    assert config.get("issues.claim-attempts") == 3
    assert config.get("issues.claim-label") == "accepted"
    assert config.source("issues.preference") == "file"
    assert config.get("issues.task-dir") == tmp_path / "project/runs/issue-tasks"
    phases = cfgmod.load(write(tmp_path, json.dumps(BASE)))
    assert not any(key.startswith("issues.") for key in phases.values)
    assert phases.get("board.issues-file") is None


@pytest.mark.parametrize(
    "missing", ["repo", "label-groups", "difficulty-order", "preference", "task-dir"]
)
def test_missing_issue_required_key_names_it(tmp_path: Path, missing: str) -> None:
    document = issue_document()
    del document["issues"][missing]
    with pytest.raises(cfgmod.ConfigError, match=rf"issues\.{missing}"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


@pytest.mark.parametrize(
    "missing, expected", [("issues", "issues.repo"), ("board", "board.file")]
)
def test_issue_provider_needs_issue_object_and_board(
    tmp_path: Path, missing: str, expected: str
) -> None:
    document = issue_document()
    del document[missing]
    with pytest.raises(cfgmod.ConfigError, match=expected):
        cfgmod.load(write(tmp_path, json.dumps(document)))


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


@pytest.mark.parametrize("provider", ["phase", "issues"])
def test_a_null_trust_value_is_treated_as_absent(tmp_path: Path, provider: str) -> None:
    if provider == "issues":
        document = issue_document()
        document["issues"]["trusted-authors"] = None
    else:
        document = copy.deepcopy(BASE)
        document["issues"] = {"trusted-authors": None}
    config = cfgmod.load(write(tmp_path, json.dumps(document)))
    expected: list[str] | None = [] if provider == "issues" else None
    assert config.get("issues.trusted-authors") == expected


def test_driver_flag_is_applied_before_presence_validation(tmp_path: Path) -> None:
    document = issue_document()
    document["driver"]["provider"] = "phase"
    config = cfgmod.load(write(tmp_path, json.dumps(document)), {"driver": "issues"})
    assert config.get("driver.provider") == "issues"
    assert config.source("driver.provider") == "flag"


@pytest.mark.parametrize("preference", ["category", "search"])
def test_preference_requires_its_own_text(tmp_path: Path, preference: str) -> None:
    document = issue_document()
    document["issues"]["preference"] = preference
    with pytest.raises(cfgmod.ConfigError, match=f"issues.{preference}"):
        cfgmod.load(write(tmp_path, json.dumps(document)))
    document["issues"][preference] = (
        "area:cli" if preference == "category" else "sort:updated"
    )
    config = cfgmod.load(write(tmp_path, json.dumps(document)))
    assert config.get("issues.preference") == preference


def test_unknown_preference_names_known_choices(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["preference"] = "random"
    with pytest.raises(
        cfgmod.ConfigError, match="newest, easiest, hardest, category, search"
    ):
        cfgmod.load(write(tmp_path, json.dumps(document)))


@pytest.mark.parametrize(
    "group",
    [
        {"labels": [], "cardinality": "one"},
        {"labels": [""], "cardinality": "one"},
        {"labels": [4], "cardinality": "one"},
        {"labels": ["bug"], "cardinality": "all"},
        ["bug"],
    ],
)
def test_invalid_group_names_the_group(tmp_path: Path, group: object) -> None:
    document = issue_document()
    document["issues"]["label-groups"]["type"] = group
    with pytest.raises(cfgmod.ConfigError, match="issues.label-groups.type"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_difficulty_labels_and_order_must_match(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["difficulty-order"] = ["difficulty:easy", "difficulty:medium"]
    with pytest.raises(cfgmod.ConfigError, match="difficulty:hard, difficulty:medium"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


@pytest.mark.parametrize(
    "key, value",
    [
        ("repo", "bad"),
        ("repo", "example/project\n"),
        ("label-groups", {}),
        ("untrusted-comments", "allow"),
        ("max-comments", True),
        ("max-text-bytes", 0),
        ("list-limit", -1),
        ("timeout", 0),
        ("claim-attempts", 0),
        ("claim-label", ""),
        ("claim-label", "accepted,bug"),
        ("claim-label", "accepted\nforged"),
        ("trusted-authors", [""]),
        ("comment-trust", {"authors": [1]}),
        ("comment-trust", {"unknown": []}),
        ("category", "bug\nforged: true"),
    ],
)
def test_invalid_issue_values_fail_before_selector(
    tmp_path: Path, key: str, value: object
) -> None:
    document = issue_document()
    document["issues"][key] = value
    with pytest.raises(cfgmod.ConfigError, match=rf"issues\.{key}"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "../issues.json",
        ".git/issues.json",
        "coordination/phases.json",
        "coordination/./phases.json",
    ],
)
def test_issue_board_file_is_required_safe_and_distinct(
    tmp_path: Path, name: str | None
) -> None:
    document = issue_document()
    if name is None:
        del document["board"]["issues-file"]
    else:
        document["board"]["issues-file"] = name
    with pytest.raises(cfgmod.ConfigError, match="board.issues-file"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_task_directory_cannot_escape_checkout_through_symlink(tmp_path: Path) -> None:
    document = issue_document()
    checkout = tmp_path / "project"
    checkout.mkdir()
    (checkout / "outside").symlink_to(tmp_path, target_is_directory=True)
    document["issues"]["task-dir"] = "project/outside/tasks"
    with pytest.raises(cfgmod.ConfigError, match="issues.task-dir.*inside repo-root"):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_issue_board_symlink_cannot_reach_outside_board(tmp_path: Path) -> None:
    document = issue_document()
    board = tmp_path / "board"
    board.mkdir()
    (board / "outside").symlink_to(tmp_path, target_is_directory=True)
    document["board"]["repo"] = "board"
    document["board"]["issues-file"] = "outside/issues.json"
    with pytest.raises(
        cfgmod.ConfigError, match="board.issues-file.*inside board.repo"
    ):
        cfgmod.load(write(tmp_path, json.dumps(document)))


def test_issue_shell_and_report_render_structured_values(tmp_path: Path) -> None:
    document = issue_document()
    config = cfgmod.load(write(tmp_path, json.dumps(document)))
    lines = cfgmod.shell_lines(config)
    assert "BOARD_ISSUES_FILE=coordination/issues.json" in lines
    assert "ISSUES_REPO=example/project" in lines
    assert "ISSUES_PREFERENCE=newest" in lines
    assert "ISSUES_TIMEOUT=30" in lines
    assert "ARGUS_SOURCE_ISSUES_TIMEOUT='default'" in lines
    groups = [
        line
        for line in cfgmod.report_lines(config)
        if line.startswith("issues.label-groups\t")
    ]
    assert json.loads(groups[0].split("\t", 2)[2]) == document["issues"]["label-groups"]


def test_defaults_are_not_shared_between_loads(tmp_path: Path) -> None:
    path = write(tmp_path, json.dumps(issue_document()))
    first = cfgmod.load(path)
    authors: Any = first.get("issues.trusted-authors")
    authors.append("different")
    assert cfgmod.load(path).get("issues.trusted-authors") == []


def test_issue_repo_accepts_dot_prefixed_repository(tmp_path: Path) -> None:
    document = issue_document()
    document["issues"]["repo"] = "example/.github"
    assert (
        cfgmod.load(write(tmp_path, json.dumps(document))).get("issues.repo")
        == "example/.github"
    )
