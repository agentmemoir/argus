# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test checks for coordination checkout hygiene."""

import datetime as _datetime
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from argus.board.core import WORKTREE_PREFIX, CoordinationUnavailable
from argus.board.git import _git, git_binary
from argus.board.selftest_board import _board_branch_checks
from argus.board.selftest_fixture import _git_test_repo, _self_test_check
from argus.board.store import ReservationStore
from argus.board.worktree import sweep_stale_worktrees


def _no_identity(private: Path) -> None:
    """Take every git identity away, so the commit step cannot succeed."""
    for key in ("user.email", "user.name"):
        _git(private, "config", key, "")


def _worktree_entries(private: Path) -> str:
    rc, out, _err = _git(private, "worktree", "list")
    return out if rc == 0 else ""


def _leftover_checkouts() -> list[Path]:
    return sorted(Path(tempfile.gettempdir()).glob(f"{WORKTREE_PREFIX}*"))


def drop_checkouts_under(path: str | Path) -> int:
    """Remove kept checkouts that belong to a fixture rooted at ``path``.

    A checkout deliberately survives a rejected push, and a self-test provokes
    rejected pushes on purpose. Its fixture is torn down straight afterwards, so
    the checkout is left pointing at a repository that no longer exists and
    nothing will ever sweep it. Finding them by the fixture path in their
    ``.git`` pointer is exact: a checkout belonging to another fixture, or to a
    real machine, is not touched.
    """
    root = str(Path(path).resolve())
    dropped = 0
    for candidate in _leftover_checkouts():
        pointer = candidate / "checkout" / ".git"
        try:
            target = pointer.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if root not in target:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        dropped += 1
    return dropped


def _checkout_hygiene_checks(checks: list[dict[str, Any]], base: Path) -> None:
    """A failed commit must leave no checkout, and a rejected push must leave one.

    The two are opposites on purpose. A commit that failed has no commit, so
    its folder holds nothing and keeping it costs a directory per tick for
    ever. A rejected push holds the only copy of a board state the remote
    never received, so that one is worth a directory.
    """
    # The `pre-receive` hook below is the thing under test, so the fixture root
    # is the caller's executable directory rather than a fresh default-temp
    # directory a `noexec` mount would refuse to run it from.
    sandbox = base / "checkout-hygiene"
    sandbox.mkdir()
    try:
        before = set(_leftover_checkouts())

        # A commit that fails: no folder, and no stale registration either.
        _remote, clients = _git_test_repo(sandbox, "failed-commit")
        private = clients[0]
        root = sandbox / "failed-root"
        root.mkdir()
        _no_identity(private)
        store = ReservationStore(root, private_repo=private)
        try:
            store.reserve(100700, "server-01")
            failed = False
            detail = "the reservation was accepted without a git identity"
        except CoordinationUnavailable as exc:
            failed = "commit failed" in str(exc)
            detail = "commit step failed as intended"
        leaked = sorted(set(_leftover_checkouts()) - before)
        registrations = [
            line
            for line in _worktree_entries(private).splitlines()
            if WORKTREE_PREFIX in line
        ]
        _self_test_check(
            checks,
            "a failed board commit leaves no checkout",
            failed and not leaked,
            f"{detail}; {len(leaked)} leftover checkout(s), "
            f"{len(registrations)} stale registration(s)",
        )
        for path in leaked:
            shutil.rmtree(path, ignore_errors=True)
        _git(private, "worktree", "prune")

        # A rejected push: the local commit is the only copy, so it is kept and
        # the path is reported rather than discarded.
        rejected_root = sandbox / "rejected-root"
        rejected_root.mkdir()
        _remote2, clients2 = _git_test_repo(sandbox, "rejected-push")
        keeper = clients2[0]
        rejected = ReservationStore(rejected_root, private_repo=keeper)
        # One reservation first, so the board branch exists. A hook that refuses
        # every push would otherwise also refuse the push that creates the
        # branch, and the case would be measuring the seed rather than the
        # rejection.
        rejected.reserve(100700, "server-01")
        # A pre-receive hook on the bare remote is the honest way to reject a
        # push that git itself considers well formed.
        subprocess.run(
            [
                git_binary(),
                "-C",
                str(_remote2),
                "config",
                "core.hooksPath",
                str(sandbox / "hooks"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        hooks = sandbox / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-receive").write_text("#!/bin/sh\ncat >/dev/null\nexit 1\n")
        (hooks / "pre-receive").chmod(0o755)
        before_rejected = set(_leftover_checkouts())
        try:
            rejected.reserve(100701, "server-01")
            raised, reason = False, "the push was accepted"
        except CoordinationUnavailable as exc:
            raised = "push failed" in str(exc)
            reason = str(exc).splitlines()[0][:90]
        kept = sorted(set(_leftover_checkouts()) - before_rejected)
        # Exactly one, not "at least one": the retry loop makes four attempts and
        # only the last holds a state the remote refused. Keeping the earlier
        # ones is what this count exists to catch.
        _self_test_check(
            checks,
            "a rejected push keeps only the final attempt's checkout",
            raised and len(kept) == 1,
            f"{reason}; {len(kept)} checkout(s) kept",
        )
        for path in kept:
            shutil.rmtree(path, ignore_errors=True)
        _git(keeper, "worktree", "prune")

        # The sweep takes an old folder and leaves a recent one, because two
        # runs on one machine must not delete each other's.
        sweep_root = sandbox / "sweep"
        sweep_root.mkdir()
        old_dir = sweep_root / f"{WORKTREE_PREFIX}old"
        new_dir = sweep_root / f"{WORKTREE_PREFIX}new"
        for candidate, age in ((old_dir, 7200), (new_dir, 0)):
            candidate.mkdir()
            (candidate / "payload").write_text("x" * 4096)
            moment = _datetime.datetime.now(_datetime.UTC).timestamp() - age
            os.utime(candidate, (moment, moment))
        removed, reclaimed = sweep_stale_worktrees(root=sweep_root)
        remaining = sorted(p.name for p in sweep_root.iterdir())
        _self_test_check(
            checks,
            "the sweep takes an old checkout and spares a new one",
            removed == 1 and reclaimed > 0 and remaining == [new_dir.name],
            f"removed {removed}, reclaimed {reclaimed} bytes, remaining {remaining}",
        )
        # Only this module's own prefix is ever touched.
        other = sweep_root / "someone-elses-directory"
        other.mkdir()
        moment = _datetime.datetime.now(_datetime.UTC).timestamp() - 7200
        os.utime(other, (moment, moment))
        sweep_stale_worktrees(root=sweep_root)
        _self_test_check(
            checks,
            "the sweep spares a directory it did not create",
            other.exists(),
            str(other.name),
        )
        _board_branch_checks(checks, sandbox)
    finally:
        drop_checkouts_under(sandbox)
        shutil.rmtree(sandbox, ignore_errors=True)


__all__ = ["_checkout_hygiene_checks"]
