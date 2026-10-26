# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Issue publication with the issue claim fence and plain Git pushes."""

from pathlib import Path
from typing import Any

from argus.board.reservations import (
    CoordinationError,
    ReservationFenced,
    ReservationStore,
)
from argus.gitsync.git import (
    ConfigError,
    _is_private_path,
    current_branch,
    dirty_paths,
    git,
    resolve_upstream,
    summarize,
)
from argus.gitsync.group_publication import _push_one
from argus.gitsync.issue_paths import (
    issue_branch,
    issue_number,
    issue_scope,
    load_issue_reservation,
)
from argus.gitsync.issue_runstate import commit_issue_state
from argus.gitsync.runstate import ENGINE_WRITTEN_RUN_ARTIFACTS
from argus.gitsync.sync import mode_resync, sync_all
from argus.issues.claims import snapshot


def publish_issue(
    root: str | Path,
    number: int | str,
    *,
    reservation_file: str | Path | None = None,
    machine_id: str | None = None,
    reservation_id: str | None = None,
    generation: int | None = None,
    pull_request: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    number = issue_number(number)
    private, scopes = issue_scope(root, number)
    branch = issue_branch(number, scopes[1])
    if current_branch(root) != branch:
        raise ConfigError("public checkout is not on the current issue branch")
    _branch, _remote, remote_branch, why = resolve_upstream(root)
    if why or remote_branch != branch:
        raise ConfigError(why or "issue upstream names a different branch")
    results: list[dict[str, Any]] = []
    info: dict[str, Any] = {
        "mode": "publish",
        "kind": "issue",
        "issue": number,
        "branch": branch,
        "repos": results,
    }
    try:
        reservation = load_issue_reservation(
            root,
            number,
            path=reservation_file,
            machine_id=machine_id,
            reservation_id=reservation_id,
            generation=generation,
        )
        store = ReservationStore(root, issues=True)

        def fence() -> dict[str, Any]:
            return store.assert_owner(reservation)

        fence()
        if snapshot(root, number, reservation) != private / scopes[1]:
            raise ConfigError("frozen issue task does not belong to the current claim")
        public_dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
        if public_dirty:
            return dict(
                info,
                ok=False,
                state="RETRY",
                error="public checkout has uncommitted changes",
                dirty=public_dirty,
            )
        # Issue claims use lifecycle renewal. The shared publication lock belongs
        # to phases, so every issue push checks the owner again instead.
        # Prove distinct destinations before the first run-state push.
        synced = sync_all(root, "push", branch=branch)
        results.append(synced)
        if not synced["ok"]:
            return dict(info, ok=False, state="RETRY", summary=synced["summary"])
        clean = commit_issue_state(root, number, before_push=fence)
        results.append(clean)
        if not clean["ok"]:
            return dict(
                info, ok=False, state="RETRY", summary=summarize([clean], "publish")
            )
        run = private / scopes[0]
        dirty = [private / p for p in dirty_paths(private)]
        tracked = set(git(private, "ls-files", "-z", "--", scopes[0])[1].split("\0"))
        staged = set(
            git(private, "diff", "--cached", "--name-only", "-z")[1].split("\0")
        )
        churn = [
            str(p)
            for p in dirty
            if p.parent == run
            and p.name in ENGINE_WRITTEN_RUN_ARTIFACTS
            and p.relative_to(private).as_posix() in tracked
            and p.relative_to(private).as_posix() not in staged
        ]
        real = [str(p) for p in dirty if str(p) not in churn]
        if real:
            return dict(
                info,
                ok=False,
                state="RETRY",
                error="working tree is dirty after issue cleanup",
                dirty=real,
                engine_written=churn,
            )
        resync = mode_resync(root)
        results.append(resync)
        if not resync["ok"]:
            return dict(info, ok=False, state="RETRY", summary=resync["summary"])
        synced = sync_all(root, "push", branch=branch)
        results.append(synced)
        if not synced["ok"]:
            return dict(info, ok=False, state="RETRY", summary=synced["summary"])
        for checkout in (root, private):
            fence()
            pushed = _push_one(checkout)
            results.append(pushed)
            if not pushed["ok"]:
                return dict(
                    info,
                    ok=False,
                    state="RETRY",
                    summary=summarize([pushed], "publish"),
                )
        return dict(
            info,
            ok=True,
            state="AWAITING_REVIEW",
            pull_request=pull_request,
            engine_written=churn,
            summary=summarize(results[-2:], "publish"),
        )
    except ReservationFenced as exc:
        return dict(info, ok=False, state="FENCED", error=str(exc))
    except CoordinationError as exc:
        return dict(info, ok=False, state="COORDINATION_UNAVAILABLE", error=str(exc))
