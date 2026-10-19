# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Return a released issue checkout to its integration branch without GitHub reads."""

import re
from pathlib import Path
from typing import Any

from argus.gitsync.git import (
    blocked,
    current_branch,
    dirty_paths,
    integration_branch,
    resolve_upstream,
    summarize,
)
from argus.gitsync.issue_paths import issue_number


def tidy_issue_checkout(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    here = current_branch(root)
    info: dict[str, Any] = {"repo": str(root), "branch": here}
    if here == integration_branch(root):
        return dict(info, ok=True, action="nothing-to-tidy")
    match = re.fullmatch(r"issue/([0-9]+)-[a-z0-9]+(?:-[a-z0-9]+)*", here)
    if match is None:
        return blocked(info, "checkout is not on an issue branch")
    number = issue_number(match.group(1))
    info["issue"] = number
    dirty = dirty_paths(root)
    if dirty:
        return blocked(info, "the working tree has uncommitted changes", dirty=dirty)
    _branch, remote, remote_branch, why = resolve_upstream(root)
    if why or remote is None or remote_branch != here:
        return blocked(info, why or "issue branch upstream does not match its branch")
    # Publication pushes the issue branch and ends there; Argus opens no pull
    # request and closes no issue, so there is no GitHub evidence to read here.
    return dict(info, ok=True, action="issue-not-yet-published")


def mode_issue_tidy(root: str | Path) -> dict[str, Any]:
    result = tidy_issue_checkout(root)
    return {
        "ok": result["ok"],
        "mode": "tidy",
        "kind": "issue",
        "repos": [result],
        "summary": summarize([result], "tidy"),
    }
