# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test checks for the push boundary and publication lock."""

import subprocess

from argus.phase_reservations_core import CoordinationUnavailable, PublicationBusy
from argus.phase_reservations_git import _git, git_binary
from argus.phase_reservations_store import ReservationStore


def _pushurl_boundary_check(base):
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


def _publication_lock_check(store, reservation, contender):
    store.acquire_publication(reservation)
    try:
        store.acquire_publication(contender)
        blocked = False
    except PublicationBusy:
        blocked = True
    store.release_publication(reservation)
    return blocked
