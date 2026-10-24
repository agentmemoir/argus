# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Render a claimed issue's task file again with the first-selection renderer."""

from pathlib import Path

from argus import conventions
from argus.issues.client import IssueClient
from argus.issues.filter import label_names
from argus.issues.task import render, task_directory, task_path, write
from argus.issues.transport import IssuesUnavailable
from argus.issues.trust import Trust


def rerender_task(repo: str | Path, number: int) -> Path:
    """Write the issue's task file the way first selection does, and return it."""
    root = Path(repo).resolve()
    client = IssueClient()
    detail = client.view(number)
    matched = label_names(detail).intersection(
        conventions.setting("issues.difficulty-order")
    )
    if len(matched) != 1:
        raise IssuesUnavailable(
            "configuration", "difficulty must match one ordering label"
        )
    detail["difficulty"] = next(iter(matched))
    path = task_path(task_directory(root), detail)
    write(
        path,
        render(
            detail,
            client,
            Trust(root, client),
            conventions.setting("issues.label-groups"),
            conventions.setting("issues.preference"),
        ),
    )
    return path
