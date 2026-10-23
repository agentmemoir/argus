# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The head commit of one pull request, read once from gh."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from argus.gitsync.gh_target import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary

_SHA = re.compile(r"[0-9a-f]{40}")


def gh_pull_request_head(
    root: Path,
    target: int | str,
    owner: str | None = None,
    repo: str | None = None,
    host: str | None = None,
) -> str | None:
    """The head commit SHA of one pull request, or ``None`` when gh did not say.

    A missing owner or repo, a failed call, a timeout, or an answer without a
    full SHA all return ``None``, so the caller treats the head as unread. gh's
    stderr is never read into the result or a log.
    """
    if not owner or not repo:
        return None
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(target),
        "--json",
        "headRefOid",
        "--repo",
        repo_target(owner, repo, host),
    ]
    try:
        done = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode:
        return None
    try:
        sha = json.loads(done.stdout).get("headRefOid")
    except (ValueError, AttributeError):
        return None
    return sha if isinstance(sha, str) and _SHA.fullmatch(sha) else None
