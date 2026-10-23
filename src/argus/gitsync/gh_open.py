# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The open pull requests of one repository, from one gh list call."""

import json
import subprocess
from pathlib import Path
from typing import cast

from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.gh_read import PR_LIST_LIMIT
from argus.gitsync.gh_target import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def gh_open_pull_request_numbers(
    root: Path, owner: str | None, repo: str | None, host: str | None
) -> tuple[dict[int, bool] | None, str]:
    """The repository's open pull requests and whether each is a draft, or why not.

    Returns a map from pull request number to its draft state, and an empty
    detail, or ``None`` and the reason. ``None`` covers a failed call, a reply
    that is not a list of rows with a number and a draft state, and a reply that
    fills the row limit: that list may be cut short, and a cut list would read a
    live pull request as closed. An empty map really means no pull request is
    open.
    """
    argv = [
        gh_binary(),
        "pr",
        "list",
        "--repo",
        repo_target(owner, repo, host),
        "--state",
        "open",
        "--limit",
        str(PR_LIST_LIMIT),
        "--json",
        "number,isDraft",
    ]
    try:
        done = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return None, gh_exit_detail(done.returncode, "the open list")
    try:
        parsed: object = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    if not isinstance(parsed, list):
        return None, "gh returned JSON that is not a list"
    rows = cast("list[object]", parsed)
    drafts: dict[int, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None, "gh listed a row without a pull request number or draft state"
        fields = cast("dict[str, object]", row)
        number, draft = fields.get("number"), fields.get("isDraft")
        if not isinstance(number, int) or not isinstance(draft, bool):
            return None, "gh listed a row without a pull request number or draft state"
        drafts[number] = draft
    if len(rows) >= PR_LIST_LIMIT:
        return None, "gh filled the row limit, so the open list may be cut short"
    return drafts, ""


__all__ = ["gh_open_pull_request_numbers"]
