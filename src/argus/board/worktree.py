# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-lived worktrees and the read-modify-push transaction."""

import contextlib
import datetime as _datetime
import shutil
import sys
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from argus.board.branch import (
    _fetch_branch_ref,
    _push_backup,
    _state_initialized,
)
from argus.board.core import (
    FETCH_TIMEOUT_S,
    MAX_TRANSACTION_ATTEMPTS,
    WORKTREE_PREFIX,
    WORKTREE_SWEEP_AGE_S,
    CoordinationUnavailable,
    _Mutation,
    _safe_detail,
)
from argus.board.git import _git, _head, _is_git_repo
from argus.board.seed import ensure_board_branch
from argus.board.state import (
    _read_state_file,
    _write_state_file,
    state_path,
    state_rel,
)


class _Checkout:
    """A disposable coordination checkout, and whether it is worth keeping.

    ``keep`` is the policy switch, and it is set by the caller rather than
    inferred here, because only the caller knows what the checkout ended up
    holding. A checkout whose board state was committed and whose push the
    remote rejected holds a state the remote never received, which is the one
    case a person might want to look at. A checkout whose commit failed holds
    nothing at all, because the commit is what would have held it.
    """

    def __init__(self, private: Path, parent: Path, path: Path) -> None:
        self.private = private
        self.parent = parent
        self.path = path
        self.keep = False
        #: Set when the checkout could not be removed, so the caller can name
        #: the folder in whatever it reports about the failure.
        self.stranded: str | None = None

    def __fspath__(self) -> str:
        return str(self.path)


@contextlib.contextmanager
def _detached_worktree(private: Path, ref: str) -> Generator[_Checkout, None, None]:
    """Yield a disposable checkout without taking the phase branch."""
    # A worktree parent holds the checkout's files; git never runs a file from it
    # (hooks come from the repository's own hooksPath, not this folder), so this
    # path needs no execution probe. `sweep_stale_worktrees` owns its cleanup.
    parent = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX))
    checkout = _Checkout(private, parent, parent / "checkout")
    rc, _, err = _git(private, "worktree", "add", "--detach", str(checkout.path), ref)
    if rc != 0:
        shutil.rmtree(parent, ignore_errors=True)
        raise CoordinationUnavailable(
            f"cannot create coordination worktree: {_safe_detail(err)}"
        )
    try:
        yield checkout
        kept = checkout.keep
    except BaseException:
        # An error inside the block is what makes this leak: the board state has
        # been written and staged, so the tree is dirty and an ordinary remove
        # refuses. There is nothing to inspect, because the commit that would
        # have held the state does not exist. Reset first, so the same removal
        # that works on the happy path works here too.
        _discard_checkout_contents(checkout)
        _remove_checkout(checkout)
        raise
    if kept:
        # Deliberate: the local commit is the only copy of a board state the
        # remote never received, so it stays for a person to read.
        print(
            f"coordination checkout kept for inspection: {checkout.parent}",
            file=sys.stderr,
        )
        return
    _remove_checkout(checkout)


def _discard_checkout_contents(checkout: _Checkout) -> None:
    """Make a dirty checkout clean again, so an ordinary removal can succeed."""
    _git(checkout.path, "reset", "--hard", "--quiet", "HEAD")
    _git(checkout.path, "clean", "-fdq")


def _remove_checkout(checkout: _Checkout) -> None:
    """Remove the checkout and its folder, and report it if that is impossible.

    The path is created and owned here. Removing it must not touch the phase
    checkout, even when the branch is named master, so the removal always names
    the path and never the branch.
    """
    rc, _, _ = _git(checkout.private, "worktree", "remove", str(checkout.path))
    if rc == 0:
        shutil.rmtree(checkout.parent, ignore_errors=True)
        return
    # The folder is still there, so prune alone would drop nothing: the
    # registration is accurate. Force the removal, because a directory this
    # function created and no transaction is using has no value, then prune in
    # case the force left an entry behind.
    forced, _, _ = _git(
        checkout.private, "worktree", "remove", "--force", str(checkout.path)
    )
    _git(checkout.private, "worktree", "prune")
    if forced == 0:
        shutil.rmtree(checkout.parent, ignore_errors=True)
        return
    print(
        f"coordination checkout could not be removed and is still at "
        f"{checkout.parent}; the sweep clears it once it is "
        f"{WORKTREE_SWEEP_AGE_S // 60} minutes old",
        file=sys.stderr,
    )


def sweep_stale_worktrees(
    now: float | None = None,
    age_s: int = WORKTREE_SWEEP_AGE_S,
    root: str | Path | None = None,
) -> tuple[int, int]:
    """Delete leftover coordination checkouts older than ``age_s``.

    A checkout is left behind when the machine lost power mid-transaction, and
    nothing else ever returns for it. This runs before each transaction so the
    cost of a crash is one folder rather than one per tick for ever. It only
    touches directories this module created, matched by prefix, and only ones
    old enough that no live transaction can still be using them.

    Returns ``(removed, bytes)`` so a caller can report the reclaim rather than
    leaving a slow disk loss that nobody attributes.
    """
    parent = Path(root) if root is not None else Path(tempfile.gettempdir())
    moment = (
        now if now is not None else _datetime.datetime.now(_datetime.UTC).timestamp()
    )
    removed, reclaimed = 0, 0
    try:
        candidates = list(parent.glob(f"{WORKTREE_PREFIX}*"))
    except OSError:
        return 0, 0
    for candidate in candidates:
        try:
            age = moment - candidate.stat().st_mtime
        except OSError:
            continue
        if age < age_s:
            continue
        reclaimed += _directory_bytes(candidate)
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed, reclaimed


def _directory_bytes(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _commit_state(checkout: Path, message: str, *, issues: bool = False) -> str | None:
    rc, _, err = _git(checkout, "add", "--", state_rel(issues=issues).as_posix())
    if rc != 0:
        raise CoordinationUnavailable(
            f"cannot stage coordination state: {_safe_detail(err)}"
        )
    rc, out, err = _git(checkout, "commit", "-q", "-m", message)
    if rc != 0:
        combined = _safe_detail(f"{out} {err}")
        if "nothing to commit" in combined.lower():
            return None
        raise CoordinationUnavailable(f"coordination state commit failed: {combined}")
    rc, commit, _ = _git(checkout, "rev-parse", "HEAD")
    if rc != 0 or not commit:
        raise CoordinationUnavailable("coordination commit has no revision")
    return commit


def _transaction(
    private: Path,
    remote: str,
    branch: str,
    mutate: Callable[[dict[str, Any]], _Mutation],
    message: str,
    *,
    issues: bool = False,
) -> Any:
    """Run a read-modify-push transaction, retrying only rejected pushes."""
    last_error = None
    # A transaction is a write, so it is the path allowed to create the board's
    # branch. It is done once here rather than per attempt, because the first
    # attempt either finds the branch or creates it and no attempt after that
    # has anything left to decide.
    ensure_board_branch(private, remote, branch, issues=issues)
    swept, reclaimed = sweep_stale_worktrees()
    if swept:
        # A leak then reads as a number on the tick that found it, rather than
        # as a disk filling up that nobody attributes to anything.
        print(
            f"coordination sweep removed {swept} stale checkout(s), "
            f"{reclaimed // (1024 * 1024)} MB reclaimed",
            file=sys.stderr,
        )
    for attempt in range(MAX_TRANSACTION_ATTEMPTS):
        ref, _commit = _fetch_branch_ref(private, remote, branch)
        initialized = _state_initialized(private, ref, issues=issues)
        with _detached_worktree(private, ref) as checkout:
            state = _read_state_file(
                state_path(checkout.path, issues=issues),
                allow_missing=not initialized,
                issues=issues,
            )
            mutation = mutate(state)
            if not mutation.changed:
                return mutation.value
            _write_state_file(
                state_path(checkout.path, issues=issues), state, issues=issues
            )
            new_commit = _commit_state(checkout.path, message, issues=issues)
            if new_commit is None:
                return mutation.value
            rc, out, err = _git(
                checkout.path,
                "push",
                remote,
                f"HEAD:refs/heads/{branch}",
                timeout=FETCH_TIMEOUT_S,
            )
            # The backup ref is advanced only after the board push succeeds, so
            # a rejected board push cannot move the backup. The backup push is
            # not forced and its failure is only a warning, so a foreign ref or
            # a server without atomic support never blocks a board write.
            if rc == 0:
                _push_backup(checkout.path, remote, branch, new_commit)
            # The final attempt's commit exists and the remote refused it, so its
            # folder holds the only copy of a board state nobody else has. The
            # earlier attempts are not kept: each one holds an intermediate state
            # the final attempt already superseded, and each is a whole checkout
            # of the private repository, so keeping them costs four directories
            # per rejected transaction to preserve nothing the last one lacks.
            checkout.keep = rc != 0 and attempt == MAX_TRANSACTION_ATTEMPTS - 1
        if rc == 0:
            return mutation.value
        last_error = _safe_detail(f"{out} {err}")
    raise CoordinationUnavailable(
        f"coordination state push failed after retries: {last_error or 'unknown error'}"
    )


def _base_revisions(root: Path, private: Path) -> dict[str, Any]:
    public = _head(root) if _is_git_repo(root) else None
    private_revision = _head(private)
    return {"public": public, "private": private_revision}


__all__ = ["_base_revisions", "_detached_worktree", "_transaction"]
