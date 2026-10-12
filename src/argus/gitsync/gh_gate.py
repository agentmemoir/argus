# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The GitHub CLI reads and writes the group gate uses: open pull requests on a branch, a pull request's checks, its comments, and a guarded merge."""

import json
import subprocess
from typing import Any

from argus.gitsync.gh import _repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary

_LIST_FIELDS = (
    "number,url,title,isDraft,isCrossRepository,mergeable,reviewDecision,headRefOid"
)


def _gh(argv: list[str]) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """Run one gh command. Returns the finished process, or ``None`` and why not.

    The same two outcomes ``gh.py`` reports for every call: gh is missing, or it
    did not answer in time. A non-zero exit is left to the caller, because
    ``gh pr checks`` exits non-zero on a red or running check and still prints
    the answer. Stderr is captured and never reported, for the reason
    ``_gh_pull_request_view`` gives.
    """
    try:
        done = subprocess.run(
            [gh_binary(), *argv], capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    return done, ""


def _json_list(done: subprocess.CompletedProcess[str]) -> list[dict[str, Any]] | None:
    """The JSON array gh printed, or ``None`` when it printed anything else."""
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return None
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        return None
    return raw


def gh_open_pull_requests(
    base: str, owner: str | None, repo: str | None, host: str | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    """The open pull requests whose base is ``base``, or ``None`` and why not.

    ``None`` is not an empty list: a caller that read "could not ask" as "none
    open" would publish past a fix that is waiting.
    """
    done, why = _gh(
        ["pr", "list", "--repo", _repo_target(owner, repo, host)]
        + ["--base", base, "--state", "open", "--limit", "100", "--json", _LIST_FIELDS]
    )
    if done is None:
        return None, why
    if done.returncode != 0:
        return None, f"gh exited {done.returncode}"
    rows = _json_list(done)
    return (rows, "") if rows is not None else (None, "gh did not return a list")


def gh_pull_request_checks(
    number: int | str, owner: str | None, repo: str | None, host: str | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    """The checks on a pull request's current head, or ``None`` and why not.

    Each row carries ``name`` and ``bucket`` (``pass``, ``fail``, ``pending``,
    ``skipping`` or ``cancel``). The exit status is not read: gh exits 1 for a
    failing check and 8 for a running one, with the rows printed either way. A
    pull request with no checks prints no rows, and reads as unanswered here.
    """
    done, why = _gh(
        ["pr", "checks", str(int(number)), "--json", "name,bucket"]
        + ["--repo", _repo_target(owner, repo, host)]
    )
    if done is None:
        return None, why
    rows = _json_list(done)
    return (rows, "") if rows else (None, "gh reported no checks")


def gh_pull_request_comment_bodies(
    number: int | str, owner: str | None, repo: str | None, host: str | None = None
) -> tuple[list[str] | None, str]:
    """Every comment body on a pull request, or ``None`` and why not."""
    done, why = _gh(
        ["pr", "view", str(int(number)), "--json", "comments"]
        + ["--repo", _repo_target(owner, repo, host)]
    )
    if done is None:
        return None, why
    try:
        raw = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        raw = None
    comments = raw.get("comments") if isinstance(raw, dict) else None
    if not isinstance(comments, list):
        return None, f"gh exited {done.returncode}"
    return [str(c.get("body", "")) for c in comments if isinstance(c, dict)], ""


def gh_merge_pull_request(
    number: int | str,
    head: str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[bool, str]:
    """Merge a pull request, only if its head is still ``head``.

    ``--match-head-commit`` is what makes the checks that were read a moment ago
    the checks of what is merged: a push between the read and the merge makes
    gh refuse. Never ``--admin``, so branch protection still applies.
    """
    done, why = _gh(
        ["pr", "merge", str(int(number)), "--merge", "--match-head-commit", head]
        + ["--repo", _repo_target(owner, repo, host)]
    )
    if done is None:
        return False, why
    if done.returncode != 0:
        return False, f"gh exited {done.returncode}"
    return True, f"pull request {int(number)} merged"


__all__ = [
    "gh_merge_pull_request",
    "gh_open_pull_requests",
    "gh_pull_request_checks",
    "gh_pull_request_comment_bodies",
]
