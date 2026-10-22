# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The gh read of one pull request body."""

import json
import subprocess
from typing import Any, cast

from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.gh_target import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def gh_pull_request_body(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[str | None, str]:
    """Read a pull request body, or None when GitHub cannot answer.

    Returns ``(body, detail)`` where body is the text gh reports. None is not
    empty: a caller that reads "could not ask" as empty would wipe the body.
    """
    target = repo_target(owner, repo, host)
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(int(number)),
        "--repo",
        target,
        "--json",
        "body",
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return None, gh_exit_detail(done.returncode, "the body read")
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    if not isinstance(raw, dict):
        return None, "gh returned JSON without a body"
    body_value = cast("dict[str, Any]", raw).get("body")
    if not isinstance(body_value, str):
        return None, "gh returned JSON without a body"
    return body_value, ""
