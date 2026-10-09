# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Five configured ordering choices with numeric issue tie breaks."""

from datetime import datetime
from typing import Any

from argus.issues.filter import label_names
from argus.issues.transport import IssuesUnavailable

PREFERENCES = ("newest", "easiest", "hardest", "category", "search")


def ordering(
    issues: list[dict[str, Any]], preference: str, difficulty: list[str]
) -> list[dict[str, Any]]:
    if preference not in PREFERENCES:
        raise IssuesUnavailable(
            "configuration",
            "unknown issues.preference; known preferences: " + ", ".join(PREFERENCES),
        )
    ordered: list[dict[str, Any]] = []
    for rank, issue in enumerate(issues):
        try:
            created = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError("timestamp needs timezone")
        except ValueError as exc:
            raise IssuesUnavailable("invalid_response", "issue createdAt") from exc
        matched = label_names(issue).intersection(difficulty)
        if len(matched) != 1:
            raise IssuesUnavailable(
                "configuration", "difficulty must match one ordering label"
            )
        level = difficulty.index(next(iter(matched)))
        key = rank if preference == "search" else -created.timestamp()
        if preference in {"easiest", "hardest"}:
            key = level if preference == "easiest" else -level
        ordered.append({**issue, "ordering_key": key, "difficulty": difficulty[level]})
    return sorted(ordered, key=lambda item: (item["ordering_key"], item["number"]))
