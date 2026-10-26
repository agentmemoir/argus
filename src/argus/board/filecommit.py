# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A read-modify-push transaction for files that live outside the board.

The board's own transaction edits one known state file. A file that belongs to
another repository, such as the phases index, needs the same shape without the
board's state handling, so this module carries the general form: fetch, edit a
detached checkout, commit and push, retrying only a refused push.
"""

import sys
from collections.abc import Callable
from pathlib import Path

from argus.board.branch import _fetch_branch_ref
from argus.board.core import (
    FETCH_TIMEOUT_S,
    MAX_TRANSACTION_ATTEMPTS,
    CoordinationUnavailable,
    _safe_detail,
)
from argus.board.git import _git
from argus.board.worktree import _detached_worktree, sweep_stale_worktrees


def _commit_paths(checkout: Path, paths: list[str], message: str) -> str | None:
    """Stage exactly ``paths`` and commit them; ``None`` when nothing changed."""
    named = ", ".join(paths)
    rc, _, err = _git(checkout, "add", "--", *paths)
    if rc != 0:
        raise CoordinationUnavailable(f"cannot stage {named}: {_safe_detail(err)}")
    rc, out, err = _git(checkout, "commit", "-q", "-m", message)
    if rc != 0:
        combined = _safe_detail(f"{out} {err}")
        if "nothing to commit" in combined.lower():
            return None
        raise CoordinationUnavailable(f"cannot commit {named}: {combined}")
    rc, commit, _ = _git(checkout, "rev-parse", "HEAD")
    if rc != 0 or not commit:
        raise CoordinationUnavailable(f"cannot commit {named}: no revision")
    return commit


def file_transaction(
    repo: Path,
    remote: str,
    branch: str,
    paths: list[str | Path],
    write: Callable[[Path], bool],
    message: str,
) -> None:
    """Commit and push exactly ``paths`` under a fresh fetch, retrying a refusal.

    The caller names the repository, the branch and the paths, and supplies
    ``write``: a callback that runs inside the freshly fetched checkout and
    returns whether it changed anything. The callback is re-run on every
    attempt against a checkout of the ref that was just fetched, so a write
    that lost a race is re-applied to the new tip rather than pushed over it,
    and the refspec is never forced, so the remote is never overwritten.

    After the last attempt it fails closed and keeps the checkout, because the
    local commit is then the only copy of a state the remote refused. The
    branch must already exist on the remote, unlike a board branch: a phases
    branch is the repository's own branch, and creating it from an unrelated
    integration tip would publish a branch nobody asked for.
    """
    last_error = None
    swept, reclaimed = sweep_stale_worktrees()
    if swept:
        # A leak then reads as a number on the tick that found it, rather than
        # as a disk filling up that nobody attributes to anything.
        print(
            f"coordination sweep removed {swept} stale checkout(s), "
            f"{reclaimed // (1024 * 1024)} MB reclaimed",
            file=sys.stderr,
        )
    staged = [Path(path).as_posix() for path in paths]
    for attempt in range(MAX_TRANSACTION_ATTEMPTS):
        ref, _commit = _fetch_branch_ref(repo, remote, branch)
        with _detached_worktree(repo, ref) as checkout:
            changed = write(checkout.path)
            if not changed:
                return
            new_commit = _commit_paths(checkout.path, staged, message)
            if new_commit is None:
                return
            rc, out, err = _git(
                checkout.path,
                "push",
                remote,
                f"HEAD:refs/heads/{branch}",
                timeout=FETCH_TIMEOUT_S,
            )
            # Only the final attempt's checkout is worth keeping: it holds the
            # one local commit nobody else has, while an earlier attempt holds
            # an intermediate state the final attempt has already superseded.
            checkout.keep = rc != 0 and attempt == MAX_TRANSACTION_ATTEMPTS - 1
        if rc == 0:
            return
        last_error = _safe_detail(f"{out} {err}")
    raise CoordinationUnavailable(
        f"cannot push {', '.join(staged)} after retries: "
        f"{last_error or 'unknown error'}"
    )


__all__ = ["file_transaction"]
