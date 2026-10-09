#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared, fenced phase reservations backed by a configured Git board.

The board checkout, file, branch and remote come from the loaded config. The
board branch holds no phase work; it is never checked out as a phase branch.
Every write uses a short-lived detached Git worktree and a normal, non-forced
push. A rejected push is retried from a fresh fetch, so one client can win a
claim without overwriting another.

The board had its own branch because it used to share the integration branch
with run state and phase notes.  A reservation, a heartbeat and a status change
were each a commit there, so the branch a phase's commits were based on moved
every time the board changed, and publishing a finished phase could be rejected
as non-fast-forward by a bookkeeping write.  The first transaction after the
move seeds the branch from the integration branch's tip, which carries the
existing state file across, so no live reservation is lost.

A reservation is an ownership record, not completion evidence. The pipeline
must publish the current phase and a matching ``publication.json`` receipt
before the record can change to ``completed``.

The module is intentionally usable both as a library and as a small CLI.
The CLI is useful for registering a phase that was already running before
coordination was enabled, and for explicit operator takeover when a process
has stopped without releasing its claim.
"""

import sys

if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus.board.branch import (
    ensure_board_branch,
)
from argus.board.cli import (
    main,
)
from argus.board.core import (
    ACTIVE_STATUSES,
    BRANCH_RE,
    COOLDOWN_UNTIL_FIELD,
    COORDINATION_VERSION,
    EXPIRED_STATUS,
    FETCH_TIMEOUT_S,
    GIT_TIMEOUT_S,
    GROUP_COOLDOWN_S,
    GROUP_HEARTBEAT_WINDOW_S,
    GROUP_START_WINDOW_S,
    INTEGRATION_FALLBACK,
    ISSUE_KEY_RE,
    MACHINE_ID_RE,
    MAX_TRANSACTION_ATTEMPTS,
    PHASE_KEY_RE,
    PUBLICATION_NAME,
    REMOTE_RE,
    RESERVATION_ID_RE,
    SELF_TEST_BRANCH,
    STATUSES,
    TERMINAL_STATUSES,
    WORKTREE_PREFIX,
    WORKTREE_SWEEP_AGE_S,
    CoordinationError,
    CoordinationUnavailable,
    PublicationBusy,
    ReservationConflict,
    ReservationCooldown,
    ReservationFenced,
    _now,
)
from argus.board.files import (
    load_reservation_file,
    publication_file,
    reservation_file,
    save_reservation_file,
)
from argus.board.git import (
    git_binary,
)
from argus.board.selftest import (
    self_test,
    self_test_checks,
)
from argus.board.selftest_fixture import (
    _git_test_repo,
    _self_test_receipt,
)
from argus.board.selftest_hygiene import (
    drop_checkouts_under,
)
from argus.board.state import (
    state_path,
    state_rel,
)
from argus.board.store import (
    ReservationStore,
)
from argus.board.worktree import (
    sweep_stale_worktrees,
)

# The module surface every caller and script relied on.
__all__ = [
    "ACTIVE_STATUSES",
    "BRANCH_RE",
    "COOLDOWN_UNTIL_FIELD",
    "COORDINATION_VERSION",
    "CoordinationError",
    "CoordinationUnavailable",
    "EXPIRED_STATUS",
    "FETCH_TIMEOUT_S",
    "GIT_TIMEOUT_S",
    "GROUP_COOLDOWN_S",
    "GROUP_HEARTBEAT_WINDOW_S",
    "GROUP_START_WINDOW_S",
    "INTEGRATION_FALLBACK",
    "ISSUE_KEY_RE",
    "MACHINE_ID_RE",
    "MAX_TRANSACTION_ATTEMPTS",
    "PHASE_KEY_RE",
    "PUBLICATION_NAME",
    "PublicationBusy",
    "REMOTE_RE",
    "RESERVATION_ID_RE",
    "ReservationConflict",
    "ReservationCooldown",
    "ReservationFenced",
    "ReservationStore",
    "SELF_TEST_BRANCH",
    "STATUSES",
    "TERMINAL_STATUSES",
    "WORKTREE_PREFIX",
    "WORKTREE_SWEEP_AGE_S",
    "_git_test_repo",
    "_now",
    "_self_test_receipt",
    "drop_checkouts_under",
    "ensure_board_branch",
    "git_binary",
    "load_reservation_file",
    "main",
    "publication_file",
    "reservation_file",
    "save_reservation_file",
    "self_test",
    "self_test_checks",
    "state_path",
    "state_rel",
    "sweep_stale_worktrees",
]


if __name__ == "__main__":
    sys.exit(main())
