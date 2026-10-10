# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test checks for the push boundary and publication lock."""

import subprocess
from pathlib import Path
from typing import Any

from argus.board.core import CoordinationUnavailable, PublicationBusy
from argus.board.git import _git, git_binary
from argus.board.store import ReservationStore


def _pushurl_boundary_check(base: Path) -> tuple[bool, str]:
    public_remote = base / "public-boundary.git"
    private_remote = base / "private-boundary.git"
    for remote in (public_remote, private_remote):
        subprocess.run(
            [git_binary(), "init", "--bare", "-q", str(remote)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/master")
    public = base / "public-boundary"
    private = base / "private-boundary"
    subprocess.run(
        [git_binary(), "clone", "-q", str(public_remote), str(public)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(private)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(private, "remote", "set-url", "--push", "origin", str(public_remote))
    root = public
    try:
        ReservationStore(root, private_repo=private)
        return False, "public pushurl was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


def _boundary_walkup_check(base: Path, private: Path) -> tuple[bool, str]:
    """A public root merely inside an unrelated repository is not a public repo.

    A hardened host whose temporary directory is ``noexec`` puts the self-test
    scratch inside the checkout, so the public root is a plain directory inside
    an enclosing repository. The boundary proof must read the public
    repository's own remotes, not the enclosing one's, so construction succeeds
    and the deliberate missing board remote fails closed at ``read()``.
    """
    enclosing = base / "enclosing-repo"
    enclosing.mkdir()
    subprocess.run(
        [git_binary(), "init", "-q", str(enclosing)],
        check=True,
        capture_output=True,
        text=True,
    )
    root = enclosing / "public-root"
    root.mkdir()
    try:
        store = ReservationStore(root, private_repo=private, remote="does-not-exist")
    except CoordinationUnavailable as exc:
        return False, f"construction refused: {exc}"
    try:
        store.read()
    except CoordinationUnavailable:
        return True, "constructed, then failed closed at read()"
    return False, "read() accepted a missing remote"


def _publication_lock_check(
    store: ReservationStore,
    reservation: dict[str, Any],
    contender: dict[str, Any],
) -> bool:
    store.acquire_publication(reservation)
    try:
        store.acquire_publication(contender)
        blocked = False
    except PublicationBusy:
        blocked = True
    store.release_publication(reservation)
    return blocked


__all__ = [
    "_boundary_walkup_check",
    "_publication_lock_check",
    "_pushurl_boundary_check",
]
