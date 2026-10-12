# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The GitHub CLI reads a repair run needs: a pull request's head, and a failed run's log."""

import json
from typing import Any, cast

from argus.gitsync.gh import _repo_target
from argus.gitsync.gh_gate import _gh

# A failed run's log is megabytes, and gh may fetch it job by job, so the bound
# is well above the 15 seconds the small reads get.
GH_LOG_TIMEOUT_S = 120


def gh_pull_request_head(
    number: int | str, owner: str | None, repo: str | None, host: str | None = None
) -> tuple[tuple[str, str] | None, str]:
    """``(state, head commit)`` of a pull request, or ``None`` and why not."""
    done, why = _gh(
        ["pr", "view", str(int(number)), "--json", "state,headRefOid"]
        + ["--repo", _repo_target(owner, repo, host)]
    )
    if done is None:
        return None, why
    try:
        raw: object = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        return None, f"gh exited {done.returncode}"
    fields = cast("dict[str, Any]", raw)
    state, head = fields.get("state"), fields.get("headRefOid")
    if not isinstance(state, str) or not isinstance(head, str):
        return None, "gh returned no state or head"
    return (state, head), ""


def gh_failed_run_log(
    run: int | str, owner: str | None, repo: str | None, host: str | None = None
) -> tuple[str | None, str]:
    """The log of the failed steps of one workflow run, or ``None`` and why not."""
    done, why = _gh(
        ["run", "view", str(int(run)), "--log-failed"]
        + ["--repo", _repo_target(owner, repo, host)],
        timeout=GH_LOG_TIMEOUT_S,
    )
    if done is None:
        return None, why
    if done.returncode != 0:
        return None, f"gh exited {done.returncode}"
    return done.stdout, ""


__all__ = ["GH_LOG_TIMEOUT_S", "gh_failed_run_log", "gh_pull_request_head"]
