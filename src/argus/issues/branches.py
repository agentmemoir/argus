# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The task file, and so the branch, that a fresh issue claim works on."""

from pathlib import Path
from typing import Any

from argus.gitsync.issue_paths import issue_branch
from argus.issues.evidence import Evidence
from argus.issues.parse import issue_number
from argus.issues.task import task_path


def fresh_task_path(directory: Path, issue: dict[str, Any], evidence: Evidence) -> Path:
    """The task file for a new claim, on a branch no Argus pull request used.

    A branch an earlier pull request used is never reused. The next free numeric
    suffix is added, so the branch is issue/<n>-<slug>-2, then -3, and so on.
    """
    number = issue_number(issue["number"])
    used = {str(pull["branch"]) for pull in evidence.pull_history().get(number, [])}
    path = task_path(directory, issue)
    suffix = 1
    while issue_branch(number, path.name) in used:
        suffix += 1
        path = task_path(directory, issue, suffix)
    return path
