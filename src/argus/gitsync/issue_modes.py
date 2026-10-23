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
    current_branch,
    degraded,
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
from argus.gitsync.mode_open_pr import mode_open_pr
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


def _owned_issue_branch(root: Path) -> tuple[str, ReservationStore, Any] | None:
    """The checked-out issue branch, after this worker proves it holds the claim."""
    branch = current_branch(root)
    if not branch.startswith("issue/"):
        return None
    number = issue_number(branch.split("/", 1)[1].split("-", 1)[0])
    reservation = load_issue_reservation(root, number)
    store = ReservationStore(root, issues=True)
    store.assert_owner(reservation)
    if branch != issue_branch(number, issue_scope(root, number)[1][1]):
        raise ConfigError("checked-out branch is not the claimed issue branch")
    return branch, store, reservation


def mode_issue_checkpoint(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    private, _tasks = private_layout(root)
    owned = _owned_issue_branch(root)
    if owned is None:
        return degraded({"repo": str(root)}, "checkpoint needs an issue branch")
    branch, store, reservation = owned
    args = ["add", "-A", "--", "."]
    # git refuses a pathspec that names an ignored tree, so the exclude is
    # added only when no ignore rule already keeps the private checkout out.
    if private.is_relative_to(root):
        inner = private.relative_to(root).as_posix()
        if git(root, "check-ignore", "-q", "--", inner)[0] != 0:
            args.append(f":(exclude,literal){inner}")
    rc, out, err = git(root, *args)
    if rc != 0:
        return degraded(
            {"repo": str(root)}, f"could not stage issue work: {err or out}"
        )
    rc, staged, _err = git(root, "diff", "--cached", "--name-only")
    if rc != 0:
        return degraded({"repo": str(root)}, "cannot read the issue index")
    if staged:
        rc, out, err = git(
            root, "commit", "-q", "-m", f"wip: work in progress on {branch}"
        )
        if rc != 0:
            return degraded(
                {"repo": str(root)}, f"could not commit issue work: {err or out}"
            )
    store.assert_owner(reservation)
    return _push_one(root)


def mode_issue_open_pr(
    root: str | Path,
    title_file: str | Path | None = None,
    body_file: str | Path | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    """Open or reuse the issue branch's draft, with the phase path's readback."""
    root = Path(root).resolve()
    owned = _owned_issue_branch(root)
    if owned is None:
        return degraded({"repo": str(root)}, "open-pr needs an issue branch")
    branch, store, reservation = owned

    def recheck_claim() -> None:
        store.assert_owner(reservation)

    return mode_open_pr(
        root,
        branch=branch,
        title_file=title_file,
        body_file=body_file,
        base=base,
        before_create=recheck_claim,
    )


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
    if args.mode == "open-pr":
        return mode_issue_open_pr(
            args.root,
            title_file=args.title_file,
            body_file=args.body_file,
            base=args.base,
        )
    if args.mode == "published":
        raise ConfigError(
            "issue completion is read from GitHub; published mode is phase-only"
        )
    return None
