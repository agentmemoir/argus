# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Commit one issue run and its exact frozen task file in the private checkout."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus import conventions
from argus.gitsync.git import (
    blocked,
    dirty_paths,
    git,
    integration_branch,
    resolve_upstream,
    summarize,
)
from argus.gitsync.group_publication import _push_one
from argus.gitsync.issue_paths import (
    inside_scope,
    issue_number,
    issue_scope,
    private_layout,
)
from argus.gitsync.merge import sync_one


def commit_issue_state(
    root: str | Path,
    number: int | str,
    *,
    before_push: Callable[[], object] | None = None,
) -> dict[str, Any]:
    number = issue_number(number)
    private, scopes = issue_scope(root, number)
    info: dict[str, Any] = {"repo": str(private), "issue": number, "pathspecs": scopes}
    branch, _remote, remote_branch, why = resolve_upstream(private)
    if why:
        return blocked(info, why)
    if branch != integration_branch(private):
        return blocked(info, "private issue state must stay on the integration branch")
    if remote_branch != branch:
        return blocked(info, "private issue state upstream names a different branch")
    rc, staged, _err = git(private, "diff", "--cached", "--name-only", "-z")
    if rc != 0:
        return blocked(info, "cannot read the private index")
    outside = [p for p in staged.split("\0") if p and not inside_scope(p, scopes)]
    if outside:
        return blocked(
            info, "staged changes exist outside the current issue", staged=outside
        )
    changed = [p for p in dirty_paths(private) if inside_scope(p, scopes)]
    if changed:
        rc, out, err = git(private, "--literal-pathspecs", "add", "-A", "--", *scopes)
        if rc != 0:
            return blocked(info, f"could not stage issue state: {err or out}")
        rc, out, err = git(
            private, "commit", "-q", "-m", f"runs: close out issue {number} run state"
        )
        if rc != 0:
            return blocked(info, f"could not commit issue state: {err or out}")
    # Push even when clean: a prior attempt may have committed and failed to push.
    for attempt in (1, 2):
        if before_push is not None:
            before_push()
        result = _push_one(private)
        if result["ok"]:
            # The action names what the push did: a clean run whose push found
            # the branch already up to date reports nothing-to-push, not a push.
            return dict(
                info,
                ok=True,
                action="committed-and-pushed" if changed else result["action"],
                after=result["after"],
            )
        if attempt == 1:
            synced = sync_one(private, "start")
            if not synced["ok"]:
                return blocked(
                    info, "private remote could not be integrated", detail=synced
                )
    return blocked(info, "issue run-state push failed", detail=result)


def mode_issue_clean(
    root: str | Path, number: int | str | None = None
) -> dict[str, Any]:
    root = Path(root).resolve()
    private, _tasks = private_layout(root)
    numbers = [issue_number(number)] if number is not None else []
    if number is None:
        dirty = dirty_paths(private)
        for run in sorted(conventions.runs_dir(root).glob("issue-*")):
            match = re.fullmatch(r"issue-([0-9]+)", run.name)
            if match is None or not run.is_dir():
                continue
            candidate = issue_number(match.group(1))
            _private, scopes = issue_scope(root, candidate)
            if any(inside_scope(path, scopes) for path in dirty):
                numbers.append(candidate)
    results = [commit_issue_state(root, candidate) for candidate in numbers]
    return {
        "ok": all(r["ok"] for r in results),
        "mode": "clean",
        "kind": "issue",
        "repos": results,
        "summary": summarize(results, "clean"),
    }
