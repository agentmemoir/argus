# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The per-checkout lock that lets one board checkout fetch its branch one at a time."""

import fcntl
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from argus.board.core import FETCH_TIMEOUT_S, CoordinationUnavailable
from argus.board.git import _git

# Two processes that fetch one board branch at the same moment race on one reference
# lock: git refuses the second update with "cannot lock ref", and the read that needed
# it stops. One lock per board checkout serializes each fetch and the read of its
# reference. A lock that stays busy past the fetch timeout fails closed.
FETCH_LOCK_NAME = "argus-board-fetch.lock"
FETCH_LOCK_POLL_S = 0.05


def _fetch_lock_file(private: str | Path) -> Path:
    """The lock file in the board checkout's git directory, shared by its worktrees."""
    rc, out, _err = _git(private, "rev-parse", "--git-common-dir")
    if rc != 0 or not out.strip():
        raise CoordinationUnavailable("the board checkout has no git directory to lock")
    return Path(private, out.strip(), FETCH_LOCK_NAME)


@contextmanager
def fetch_serialized(private: str | Path, *, wait: bool = True) -> Generator[None]:
    """Hold the checkout's fetch lock, waiting at most one fetch timeout for it.

    With wait=False the lock is tried once. A caller that must not queue behind a
    running fetch, such as a stop, then fails closed at once.
    """
    lock_path = _fetch_lock_file(private)
    deadline = time.monotonic() + (FETCH_TIMEOUT_S if wait else 0.0)
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoordinationUnavailable(
                        "another process holds the board fetch lock; no fetch was made"
                    ) from None
                time.sleep(FETCH_LOCK_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
