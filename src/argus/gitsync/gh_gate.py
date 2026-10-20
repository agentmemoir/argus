# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The GitHub CLI read the group review uses: a pull request's comment bodies."""

import json
import subprocess
from typing import Any, Generic, NamedTuple, TypeVar, cast

from argus.gitsync.gh import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary

T = TypeVar("T")


class GhRead(NamedTuple, Generic[T]):
    """One gh read: the answer, or ``None`` and why it could not be read.

    Every read returns this, so the reason travels with the answer and a call
    site names it instead of reading a failed read as an empty one. A two-field
    tuple, so ``rows, why = gh_pull_request_comment_bodies(...)`` still unpacks.
    """

    value: T | None
    why: str


def _gh(
    argv: list[str], timeout: int = GH_TIMEOUT_S
) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """Run one gh command. Returns the finished process, or ``None`` and why not.

    The same two outcomes ``gh.py`` reports for every call: gh is missing, or it
    did not answer in time. A non-zero exit is left to the caller, because a gh
    read can exit non-zero and still print the answer it was asked for. Stderr
    is captured and never reported, for the reason ``_gh_pull_request_view``
    gives.
    """
    try:
        done = subprocess.run(
            [gh_binary(), *argv], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {timeout}s"
    return done, ""


def gh_pull_request_comment_bodies(
    number: int | str, owner: str | None, repo: str | None, host: str | None = None
) -> GhRead[list[str]]:
    """Every comment body on a pull request, or ``None`` and why not."""
    done, why = _gh(
        ["pr", "view", str(int(number)), "--json", "comments"]
        + ["--repo", repo_target(owner, repo, host)]
    )
    if done is None:
        return GhRead(None, why)
    try:
        raw: object = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        raw = None
    comments = (
        cast("dict[str, Any]", raw).get("comments") if isinstance(raw, dict) else None
    )
    if not isinstance(comments, list):
        return GhRead(None, f"gh exited {done.returncode}")
    bodies = [
        str(cast("dict[str, Any]", c).get("body", ""))
        for c in cast("list[object]", comments)
        if isinstance(c, dict)
    ]
    return GhRead(bodies, "")


__all__ = [
    "GhRead",
    "_gh",
    "gh_pull_request_comment_bodies",
]
