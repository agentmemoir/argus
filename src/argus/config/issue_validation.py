# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate the issue source and its board, labels, trust, and limits."""

import re
import unicodedata
from pathlib import Path
from typing import Any, cast

from argus.config.errors import ConfigError

PREFERENCES = ("newest", "easiest", "hardest", "category", "search")
TRUST_KEYS = (
    "trusted-authors",
    "trusted-apps",
    "trusted-associations",
    "codeowners-file",
    "resolve-teams",
    "comment-trust",
    "untrusted-comments",
)


def validate_presence(
    provider: object,
    issues: dict[str, Any] | None,
    board: dict[str, Any] | None,
) -> None:
    """Reject incomplete or contradictory work-source settings before loading paths."""
    if provider not in ("phase", "phases", "issues", None):
        raise ConfigError(
            "invalid value for key 'driver.provider': known drivers: phase, phases, issues"
        )
    if provider == "issues":
        if issues is None:
            raise ConfigError("missing required key 'issues.repo'")
        if board is None:
            raise ConfigError("missing required key 'board.file' for the issue source")
    elif issues is not None:
        unknown = sorted(set(issues) - set(TRUST_KEYS))
        if unknown:
            raise ConfigError(
                "'issues' contradicts 'driver.provider': select the issue source"
            )


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _strings(value: object, key: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"invalid value for key '{key}': expected string list")
    items = cast("list[object]", value)
    if (not items and not allow_empty) or not all(_text(item) for item in items):
        raise ConfigError(
            f"invalid value for key '{key}': labels must be non-empty strings"
        )
    strings = cast("list[str]", items)
    if len(strings) != len(set(strings)):
        raise ConfigError(f"invalid value for key '{key}': repeated label")
    return strings


def validate_trust(issues: dict[str, Any]) -> None:
    """Validate only the trust keys a raw ``issues`` block carries.

    A block that holds these keys alone is allowed for any provider, so this
    runs before the issue-source keys are required.
    """
    for name in ("trusted-authors", "trusted-apps", "trusted-associations"):
        value = issues.get(name)
        if value is not None:
            _strings(value, f"issues.{name}", allow_empty=True)
    comment_trust = issues.get("comment-trust")
    if comment_trust is not None:
        trust = cast("dict[str, object]", comment_trust)
        for name, entries in trust.items():
            key = f"issues.comment-trust.{name}"
            if name not in ("authors", "apps", "associations"):
                raise ConfigError(f"unknown key '{key}'")
            _strings(entries, key, allow_empty=True)
    if issues.get("untrusted-comments") not in (None, "ignore", "data-only"):
        raise ConfigError(
            "invalid value for key 'issues.untrusted-comments': use ignore or data-only"
        )


def validate_issues(values: dict[str, object]) -> None:
    """Validate the merged values after schema type and required-key checks."""
    repo = cast("str", values["issues.repo"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+", repo):
        raise ConfigError("invalid value for key 'issues.repo': expected OWNER/NAME")
    preference = values["issues.preference"]
    if preference not in PREFERENCES:
        raise ConfigError(
            "invalid value for key 'issues.preference': known preferences: "
            + ", ".join(PREFERENCES)
        )
    for name in ("category", "search"):
        key = f"issues.{name}"
        if preference == name and not _text(values.get(key)):
            raise ConfigError(f"missing required key '{key}' for preference '{name}'")
        if key in values and not _text(values[key]):
            raise ConfigError(f"invalid value for key '{key}': expected non-empty text")
    label = values["issues.claim-label"]
    # gh splits a label argument on commas and reads a leading dash as a flag.
    if not _text(label) or "," in str(label) or str(label).startswith("-"):
        raise ConfigError(
            "invalid value for key 'issues.claim-label': expected one label name"
        )
    _validate_groups(values)
    for name in (
        "max-comments",
        "max-text-bytes",
        "list-limit",
        "timeout",
        "claim-attempts",
    ):
        key = f"issues.{name}"
        if cast("int", values[key]) < 1:
            raise ConfigError(f"invalid value for key '{key}': must be positive")
    root = cast("Path", values["repo-root"])
    task = cast("Path", values["issues.task-dir"])
    if not task.is_relative_to(root):
        raise ConfigError(
            "invalid value for key 'issues.task-dir': must stay inside repo-root"
        )
    _validate_board(values)


def _validate_groups(values: dict[str, object]) -> None:
    groups = cast("dict[str, object]", values["issues.label-groups"])
    if not groups:
        raise ConfigError(
            "invalid value for key 'issues.label-groups': must not be empty"
        )
    difficulty_labels: list[str] | None = None
    for name, group in groups.items():
        key = f"issues.label-groups.{name}"
        if not _text(name) or not isinstance(group, dict):
            raise ConfigError(f"invalid label group '{key}': expected object")
        group = cast("dict[str, object]", group)
        labels = _strings(group.get("labels"), f"{key}.labels")
        if group.get("cardinality") not in ("one", "many"):
            raise ConfigError(
                f"invalid value for key '{key}.cardinality': use one or many"
            )
        unknown = set(group) - {"labels", "cardinality"}
        if unknown:
            raise ConfigError(f"unknown key '{key}.{sorted(unknown)[0]}'")
        if name == "difficulty":
            difficulty_labels = labels
    order = _strings(values["issues.difficulty-order"], "issues.difficulty-order")
    if difficulty_labels is None:
        raise ConfigError(
            "missing required label group 'issues.label-groups.difficulty'"
        )
    difference = set(difficulty_labels).symmetric_difference(order)
    if difference:
        raise ConfigError(
            "'issues.label-groups.difficulty.labels' and 'issues.difficulty-order' differ: "
            + ", ".join(sorted(difference))
        )


def _validate_board(values: dict[str, object]) -> None:
    board = cast("Path", values["board.repo"])
    name = cast("str", values["board.issues-file"])
    issue_file = Path(name)
    if (
        not name.strip()
        or issue_file.is_absolute()
        or not issue_file.parts
        or ".." in issue_file.parts
        or issue_file.parts[0] == ".git"
    ):
        raise ConfigError(
            "invalid value for key 'board.issues-file': use a safe repository-relative path"
        )
    issue_path = (board / issue_file).resolve()
    if not issue_path.is_relative_to(board):
        raise ConfigError(
            "invalid value for key 'board.issues-file': must stay inside board.repo"
        )
    phase_path = (board / cast("str", values["board.file"])).resolve()
    if issue_path == phase_path:
        raise ConfigError(
            "invalid value for key 'board.issues-file': must differ from board.file"
        )
