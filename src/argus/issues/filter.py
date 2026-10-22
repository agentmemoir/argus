# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Cheap structured filters; no title, body, or comment affects eligibility."""

from collections import Counter
from typing import Any

from argus.board.core import ACTIVE_STATUSES, EXPIRED_STATUS
from argus.issues.trust import Trust


def label_names(issue: dict[str, Any]) -> set[str]:
    return {
        label["name"] for label in issue["labels"] if isinstance(label.get("name"), str)
    }


def label_claimed(
    issue: dict[str, Any], record: dict[str, Any] | None, label: str
) -> bool:
    """A claim label stops a pick; a held or expired board claim decides alone."""
    status = (record or {}).get("status")
    if status in ACTIVE_STATUSES or status == EXPIRED_STATUS:
        return False
    # GitHub matches label names without case, so the repo's spelling counts too.
    wanted = label.casefold()
    return any(name.casefold() == wanted for name in label_names(issue))


def eligible(
    issue: dict[str, Any],
    trust: Trust,
    groups: dict[str, Any],
    difficulty: list[str],
    skipped: Counter[str],
    category: str | None = None,
) -> bool:
    reason = None
    if issue["state"].lower() != "open":
        reason = "not_open"
    elif issue["isPullRequest"]:
        reason = "is_pull_request"
    elif not trust.allows(issue):
        reason = "untrusted_author"
    if reason:
        skipped[reason] += 1
        return False
    labels = label_names(issue)
    if category and category not in labels:
        skipped["category"] += 1
        return False
    missing = [
        name
        for name, group in groups.items()
        if not labels.intersection(group["labels"])
    ]
    if missing:
        for name in missing:
            skipped[f"missing_labels.{name}"] += 1
        return False
    matched = labels.intersection(groups["difficulty"]["labels"])
    if matched - set(difficulty):
        skipped["unknown_difficulty"] += 1
        return False
    ambiguous = [
        name
        for name, group in groups.items()
        if group["cardinality"] == "one"
        and len(labels.intersection(group["labels"])) > 1
    ]
    if ambiguous:
        for name in ambiguous:
            skipped[f"{name}_ambiguous"] += 1
        return False
    return True
