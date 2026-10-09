# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The store root: construction, the board branch, and the board read."""

import json

from argus.phase_reservations_board import (
    _branch_setting,
    _fetch_branch_ref,
    _remote_setting,
    _resolve_private_repo,
    _state_initialized,
    ensure_board_branch,
)
from argus.phase_reservations_core import CoordinationUnavailable, _safe_detail
from argus.phase_reservations_git import _assert_private_boundary, _git
from argus.phase_reservations_state import _empty_state, _validate_state, state_rel


class _StoreBase:
    def __init__(self, repo_root, private_repo=None, branch=None, remote=None):
        self.root, self.private = _resolve_private_repo(repo_root, private_repo)
        self.branch = _branch_setting(branch)
        self.remote = _remote_setting(remote)
        _assert_private_boundary(self.root, self.private, self.remote)

    def migrate_board(self):
        """Create the board's branch if the remote does not have it yet.

        This is the deliberate way the branch comes into being, and it is named
        so that a caller says out loud that it means to write. The alternative
        it replaces was a read that created the branch as a side effect, which
        let inspecting the board publish to the remote.

        Returns True when this call created the branch.
        """
        return ensure_board_branch(self.private, self.remote, self.branch)

    def read(self):
        ref, commit = _fetch_branch_ref(self.private, self.remote, self.branch)
        initialized = _state_initialized(self.private, ref)
        exists_rc, _, _ = _git(
            self.private, "cat-file", "-e", f"{ref}:{state_rel().as_posix()}"
        )
        if exists_rc != 0:
            if initialized:
                raise CoordinationUnavailable(
                    "coordination state file was deleted from an initialized board"
                )
            # A board may be initialized before its first claim. The commit
            # must exist, but an absent state file is an empty state.
            return _empty_state(), commit
        rc, raw, err = _git(self.private, "show", f"{ref}:{state_rel().as_posix()}")
        if rc != 0:
            raise CoordinationUnavailable(
                f"cannot read coordination state: {_safe_detail(err or raw)}"
            )
        try:
            state = json.loads(raw)
        except ValueError as exc:
            raise CoordinationUnavailable(
                f"coordination state does not parse: {exc}"
            ) from exc
        return _validate_state(state), commit
