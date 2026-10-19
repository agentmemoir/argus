# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Creating the board branch, from its backup ref or the integration branch."""

import sys
from pathlib import Path

from argus.board.branch import (
    _backfill_backup,
    _backup_branch,
    _backup_source,
    _board_branch_exists,
    _integration_ref,
    _push_backup,
    _state_initialized,
)
from argus.board.core import (
    FETCH_TIMEOUT_S,
    CoordinationUnavailable,
    _safe_detail,
)
from argus.board.git import _git
from argus.board.state import state_rel


def ensure_board_branch(
    private: str | Path,
    remote: str,
    branch: str,
    *,
    ensure_backup: bool = False,
) -> bool:
    """Create the board's branch from the integration branch's tip, once.

    Without this the first transaction after the change fails closed on every
    machine, because the branch the board moved to does not exist yet. Seeding
    copies the whole tree at the integration branch's tip, so its history and
    every file at that tip come with it.

    A missing branch is seeded from the backup ref (``_backup_branch``) first,
    so a deleted board branch is restored with the state it last held and
    without a machine-local clone. The integration branch is the fallback, and
    only when no backup exists at all.

    The board no longer lives on the integration branch, so its state file may
    have been added there and then removed from the tip. The tip's tree then
    lacks the file while its history still has the add, and a branch seeded
    from it would read as broken: the reader sees the add and no file and fails
    closed with "coordination state file was deleted from an initialized
    board". That case is refused here, with a message that names the file and
    the recovery, so an operator re-seeds from a commit that still has the
    file. A state file that never appeared on the integration branch is the
    other case: the board is empty, which is valid, and seeding proceeds.

    The seed creates the board branch first and advances the backup ref after
    it, both with a non-forced push, so a real branch that happens to hold the
    backup name is left untouched and a backup failure is reported but does not
    fail the seed. A lost seed race leaves the other machine's branch in place
    and this call reports that it created nothing.

    This is the only function here that writes to the remote outside a board
    transaction, and it is deliberately not reachable from a read. A read that
    could create the branch would let an inspection of the board publish to the
    remote, which fails on a machine with read-only credentials and reports
    itself as a fetch failure rather than as a permission problem.

    Returns True when it created the branch. A machine that loses the race
    finds the branch already there and does nothing, which is the same answer.
    """
    if _board_branch_exists(private, remote, branch):
        if ensure_backup:
            _backfill_backup(private, remote, branch)
        return False
    backup_branch = _backup_branch(branch)
    backup = _backup_source(private, remote, branch)
    source = backup if backup is not None else _integration_ref(private, remote)
    relative = state_rel().as_posix()
    if (
        _state_initialized(private, source)
        and _git(private, "cat-file", "-e", f"{source}:{relative}")[0] != 0
    ):
        origin = (
            f"the coordination backup {backup_branch!r}"
            if backup is not None
            else "the integration branch tip"
        )
        raise CoordinationUnavailable(
            f"cannot seed coordination branch {branch!r}: {relative!r} was "
            f"removed from {origin}, so a branch seeded from it would read as "
            f"broken. Restore {branch!r} from a commit that still has the "
            f"state file."
        )
    rc, _out, err = _git(
        private,
        "push",
        remote,
        f"{source}:refs/heads/{branch}",
        timeout=FETCH_TIMEOUT_S,
    )
    if rc != 0:
        # Losing the seed race is success: the other machine made the branch.
        if _board_branch_exists(private, remote, branch):
            return False
        raise CoordinationUnavailable(
            f"cannot create coordination branch {branch!r} on {remote}: "
            f"{_safe_detail(err)}"
        )
    _push_backup(private, remote, branch, source)
    print(f"coordination branch {branch!r} seeded from {source[:12]}", file=sys.stderr)
    return True


__all__ = ["ensure_board_branch"]
