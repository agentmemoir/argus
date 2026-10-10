# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The store root: construction, the board branch, and the board read."""

import json
from pathlib import Path
from typing import Any

from argus.board.branch import (
    _branch_setting,
    _fetch_branch_ref,
    _remote_setting,
    _resolve_private_repo,
    _state_initialized,
    ensure_board_branch,
)
from argus.board.core import (
    CoordinationUnavailable,
    _issue_key,
    _phase_key,
    _safe_detail,
)
from argus.board.git import _assert_private_boundary, _git
from argus.board.state import (
    _empty_state,
    _reservation_from_record,
    _validate_state,
    state_rel,
)


class _StoreBase:
    def __init__(
        self,
        repo_root: str | Path,
        private_repo: str | Path | None = None,
        branch: str | None = None,
        remote: str | None = None,
        *,
        issues: bool = False,
    ) -> None:
        self.issues = issues
        self.kind = "issue" if issues else "phase"
        self._key = _issue_key if issues else _phase_key
        self.root, self.private = _resolve_private_repo(repo_root, private_repo)
        self.branch = _branch_setting(branch)
        self.remote = _remote_setting(remote)
        _assert_private_boundary(self.root, self.private)
        if issues:
            # Refuse a missing, unsafe or shared issue file before any board read.
            state_rel(issues=True)

    def _reservation(self, record: dict[str, Any], key: str) -> dict[str, Any]:
        return _reservation_from_record({**record, "_phase": key}, issues=self.issues)

    def migrate_board(self) -> bool:
        """Create the board's branch if the remote does not have it yet.

        This is the deliberate way the branch comes into being, and it is named
        so that a caller says out loud that it means to write. The alternative
        it replaces was a read that created the branch as a side effect, which
        let inspecting the board publish to the remote.

        Returns True when this call created the branch.
        """
        return ensure_board_branch(self.private, self.remote, self.branch)

    def read(self) -> tuple[dict[str, Any], str]:
        ref, commit = _fetch_branch_ref(self.private, self.remote, self.branch)
        initialized = _state_initialized(self.private, ref, issues=self.issues)
        exists_rc, _, _ = _git(
            self.private,
            "cat-file",
            "-e",
            f"{ref}:{state_rel(issues=self.issues).as_posix()}",
        )
        if exists_rc != 0:
            if initialized:
                raise CoordinationUnavailable(
                    "coordination state file was deleted from an initialized board"
                )
            # A board may be initialized before its first claim. The commit
            # must exist, but an absent state file is an empty state.
            return _empty_state(), commit
        rc, raw, err = _git(
            self.private, "show", f"{ref}:{state_rel(issues=self.issues).as_posix()}"
        )
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
        return _validate_state(state, issues=self.issues), commit


__all__ = ["_StoreBase"]
