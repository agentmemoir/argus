# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Bring a pull request branch up to date with its base at one fixed point.

Git decides. A clean merge is committed by git and pushed once, normally, and
never forced. A text conflict stays in progress for an agent to resolve. A read
or write that fails is a failure, never "no conflict".

Every git call is an argument list. The branch name is data: it must pass
``git check-ref-format`` and may not start with ``-``.
"""

from pathlib import Path
from typing import Any

from argus.gitsync.git import FETCH_TIMEOUT_S, git, is_git_repo

NO_CONFLICT = "NO_CONFLICT"
MERGED = "MERGED"
TEXT_CONFLICT = "TEXT_CONFLICT"
FAILED = "FAILED"

# Exit code per state. 3 means a text conflict is in progress for an agent.
EXIT_CODES = {NO_CONFLICT: 0, MERGED: 0, TEXT_CONFLICT: 3, FAILED: 1}


def _valid_branch(repo: Path, name: str) -> bool:
    if not name or name.startswith("-"):
        return False
    rc, _, _ = git(repo, "check-ref-format", f"refs/heads/{name}")
    return rc == 0


def _base_name(repo: Path) -> str | None:
    rc, out, _ = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc != 0 or not out.startswith("origin/"):
        return None
    base = out.removeprefix("origin/")
    return base if _valid_branch(repo, base) else None


def read_base(repo: Path) -> str | None:
    """The base branch named by origin/HEAD. A failed read is repaired once:
    set origin/HEAD from the remote, then read again."""
    base = _base_name(repo)
    if base is None:
        rc, _, _ = git(
            repo, "remote", "set-head", "origin", "--auto", timeout=FETCH_TIMEOUT_S
        )
        if rc == 0:
            base = _base_name(repo)
    return base


def _recorded_checkout(repo: Path, branch: str) -> str | None:
    """Why the checkout is not the recorded branch, or None when it is.

    A named checkout must be on the branch. A detached checkout must sit at the
    tip of origin/<branch>, read now.
    """
    rc, here, _ = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if rc == 0 and here:
        if here == branch:
            return None
        return f"the checkout is on {here!r}, not {branch!r}"
    _, tip, _ = git(
        repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"
    )
    _, head, _ = git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    if tip and tip == head:
        return None
    return f"the detached checkout is not at the tip of origin/{branch}"


def _failed(result: dict[str, Any], reason: str) -> dict[str, Any]:
    return dict(result, state=FAILED, reason=reason)


def base_sync(repo: str | Path, branch: str) -> dict[str, Any]:
    """Check one checkout against its base, and merge when git can do it."""
    path = Path(repo).resolve()
    result: dict[str, Any] = {
        "repo": str(path),
        "branch": branch,
        "base": None,
        "head": None,
        "conflicted": [],
    }
    if not is_git_repo(path):
        return _failed(result, "the checkout is not a git repository")
    if not _valid_branch(path, branch):
        return _failed(result, "the branch name is not a valid branch name")
    base = read_base(path)
    if base is None:
        return _failed(result, "the base branch could not be read from origin/HEAD")
    result["base"] = base
    rc, _, _ = git(path, "fetch", "origin", timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return _failed(result, "git fetch origin failed")
    why = _recorded_checkout(path, branch)
    if why is not None:
        return _failed(result, why)
    rc, _, _ = git(path, "merge-base", "--is-ancestor", f"origin/{base}", "HEAD")
    if rc == 0:
        return dict(result, state=NO_CONFLICT)
    if rc != 1:
        return _failed(result, f"origin/{base} could not be read")
    # merge-tree writes nothing to the checkout: it is the read-only test.
    rc, _, _ = git(path, "merge-tree", "--write-tree", "HEAD", f"origin/{base}")
    if rc not in (0, 1):
        return _failed(result, f"git merge-tree could not test origin/{base}")
    why = _recorded_checkout(path, branch)  # re-read right before the write
    if why is not None:
        return _failed(result, why)
    rc, _, _ = git(
        path, "merge", "--no-edit", f"origin/{base}", timeout=FETCH_TIMEOUT_S
    )
    if rc != 0:
        _, unmerged, _ = git(path, "diff", "--name-only", "--diff-filter=U")
        if unmerged:
            return dict(result, state=TEXT_CONFLICT, conflicted=unmerged.splitlines())
        return _failed(
            result, f"git merge of origin/{base} failed and left no conflict"
        )
    _, head, _ = git(path, "rev-parse", "HEAD")
    rc, _, _ = git(
        path, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=FETCH_TIMEOUT_S
    )
    if rc != 0:
        return _failed(
            result,
            f"push of {branch!r} failed; the merge commit stays local, nothing forced",
        )
    return dict(result, state=MERGED, head=head)


def report_lines(result: dict[str, Any]) -> list[str]:
    """The one-line result for the driver log, then one line per conflicted path."""
    state = result["state"]
    if state == NO_CONFLICT:
        return [f"base-sync: no conflict with {result['base']}"]
    if state == MERGED:
        return [
            f"base-sync: merged origin/{result['base']} into {result['branch']} "
            f"and pushed; new head {result['head']}"
        ]
    if state == TEXT_CONFLICT:
        head = f"base-sync: text conflict with origin/{result['base']}; "
        lines = [head + "resolve it, then commit and push"]
        return lines + [f"base-sync: conflicted path {p}" for p in result["conflicted"]]
    return [f"base-sync: failed: {result['reason']}"]
