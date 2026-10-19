# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""CLI dispatch for issue branches and publication without phase records."""

from argparse import Namespace
from pathlib import Path
from typing import Any

from argus.board.reservations import (
    CoordinationError,
    ReservationFenced,
    ReservationStore,
)
from argus.gitsync.branch import branch_phase_checkouts
from argus.gitsync.git import (
    ConfigError,
    blocked,
    current_branch,
    git,
    integration_branch,
    summarize,
)
from argus.gitsync.group_publication import _push_one
from argus.gitsync.issue_paths import (
    issue_branch,
    issue_number,
    issue_scope,
    load_issue_reservation,
    private_layout,
)
from argus.gitsync.issue_runstate import mode_issue_clean
from argus.gitsync.issue_tidy import mode_issue_tidy
from argus.gitsync.publish_issue import publish_issue
from argus.gitsync.sync import sync_all


def mode_issue_branch(
    root: str | Path, number: int | str, task_file: str | Path
) -> dict[str, Any]:
    root = Path(root).resolve()
    number = issue_number(number)
    _private, scopes = issue_scope(root, number)
    branch = issue_branch(number, scopes[1])
    if issue_branch(number, task_file) != branch:
        raise ConfigError("branch task filename differs from the frozen issue task")
    reservation = load_issue_reservation(root, number)
    store = ReservationStore(root, issues=True)
    store.assert_owner(reservation)
    synced = sync_all(root, "push")
    if not synced["ok"]:
        return dict(synced, mode="branch", kind="issue", issue=number, branch=branch)
    store.assert_owner(reservation)
    results = branch_phase_checkouts(root, branch, base=integration_branch(root))
    return {
        "ok": all(r["ok"] for r in results),
        "mode": "branch",
        "kind": "issue",
        "issue": number,
        "branch": branch,
        "base": integration_branch(root),
        "repos": results,
        "summary": summarize(results, "branch"),
    }


def mode_issue_checkpoint(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    private, _tasks = private_layout(root)
    branch = current_branch(root)
    if not branch.startswith("issue/"):
        return blocked({"repo": str(root)}, "checkpoint needs an issue branch")
    number = issue_number(branch.split("/", 1)[1].split("-", 1)[0])
    reservation = load_issue_reservation(root, number)
    store = ReservationStore(root, issues=True)
    store.assert_owner(reservation)
    args = ["add", "-A", "--", "."]
    if private.is_relative_to(root):
        args.append(f":(exclude,literal){private.relative_to(root).as_posix()}")
    rc, out, err = git(root, *args)
    if rc != 0:
        return blocked({"repo": str(root)}, f"could not stage issue work: {err or out}")
    rc, staged, _err = git(root, "diff", "--cached", "--name-only")
    if rc != 0:
        return blocked({"repo": str(root)}, "cannot read the issue index")
    if staged:
        rc, out, err = git(
            root, "commit", "-q", "-m", f"checkpoint: work in progress on {branch}"
        )
        if rc != 0:
            return blocked(
                {"repo": str(root)}, f"could not commit issue work: {err or out}"
            )
    store.assert_owner(reservation)
    return _push_one(root)


def issue_mode(args: Namespace) -> dict[str, Any] | None:
    try:
        return _issue_mode(args)
    except CoordinationError as exc:
        return {
            "ok": False,
            "mode": args.mode,
            "kind": "issue",
            "state": "FENCED"
            if isinstance(exc, ReservationFenced)
            else "COORDINATION_UNAVAILABLE",
            "error": str(exc),
        }


def _issue_mode(args: Namespace) -> dict[str, Any] | None:
    if args.mode == "branch":
        if args.phase is None or not args.phases:
            raise ConfigError(
                "--phase and --phases are required with issue branch mode"
            )
        if args.group:
            raise ConfigError("issue branches do not accept phase groups")
        return mode_issue_branch(args.root, args.phase, args.phases)
    if args.mode == "publish":
        if args.phase is None:
            raise ConfigError("--phase is required with issue publish mode")
        return publish_issue(
            args.root,
            args.phase,
            reservation_file=args.reservation_file,
            machine_id=args.machine_id,
            reservation_id=args.reservation_id,
            generation=args.generation,
            pull_request=args.pull_request,
        )
    if args.mode == "clean":
        return mode_issue_clean(args.root, args.phase)
    if args.mode == "tidy":
        return mode_issue_tidy(args.root)
    if args.mode == "checkpoint":
        return mode_issue_checkpoint(args.root)
    if args.mode == "published":
        raise ConfigError(
            "issue completion is read from GitHub; published mode is phase-only"
        )
    return None
