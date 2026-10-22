# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Placing a checkout on a phase branch, and checking both out."""

from pathlib import Path
from typing import Any

from argus.conventions import board_repo
from argus.gitsync.git import (
    current_branch,
    dirty_paths,
    git,
    integration_branch,
    is_git_repo,
    resolve_upstream,
)
from argus.gitsync.report import (
    blocked,
    degraded,
    git_failure,
)


def ensure_phase_branch(
    repo: str | Path,
    branch: str,
    base: str | None = None,
    integration_only: bool = False,
) -> dict[str, Any]:
    """Put a checkout on its phase branch, creating it from ``base`` if needed.

    Idempotent, because a driver tick runs more than once and a phase that is
    already on its branch must not be moved. Any other branch is a starting
    point when the tree is clean: nothing uncommitted can be reinterpreted
    against the commit the phase branch is cut from. A dirty tree is the narrow
    lost-work stop, on the integration branch or any other, and the message
    names the paths.

    The integration branch is the ordinary state between two phases: the
    workflow asks a person to return the checkout there once a pull request is
    open, and the tidy step does it once a phase is published. When the next
    phase stacks on an earlier phase whose pull request is still open, its base
    is that earlier phase's branch and not the integration branch, so a checkout
    sitting on the integration branch is a starting point and not a conflict.

    The index is carried across, not required to be clean, when the checkout is
    already on the base. A phase stages its work and never commits it, so the
    branch is created from the same commit the working tree is already on and
    the staged content does not move.

    ``integration_only`` marks a checkout whose contents are reviewed on the
    integration branch rather than as a pull request, which is the private
    repository. Such a checkout is left on the integration branch and reported
    as ``stays-on-integration-branch``, so the caller can still tell the two
    apart without either one being a silent no-op.

    Every outcome carries ``after_branch``, the branch the checkout is actually
    on once this returns. The caller logs it, and a log line built from the
    requested ``branch`` instead reads the same for both checkouts, so a pair
    that was branched the wrong way round still looked like a correct launch.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info: dict[str, Any] = {"repo": str(repo), "branch": branch, "base": base}
    if not is_git_repo(repo):
        return degraded(
            info, "not a git working tree; the branch step is retried next tick"
        )
    # Recorded up front so every return below, blocked ones included, reports
    # where the checkout really is rather than only what was asked of it.
    info["after_branch"] = current_branch(repo) or "(detached)"
    if integration_only:
        here = current_branch(repo)
        rc, _, err = git(repo, "rev-parse", "--verify", base)
        if rc != 0:
            return degraded(
                info,
                f"base branch {base!r} does not exist; run state is retried next tick",
            )
        if here and here != base:
            return degraded(
                info,
                f"checkout is on {here!r}, not {base!r}; the next tick retries",
            )
        return dict(
            info,
            ok=True,
            action="stays-on-integration-branch",
            after=git(repo, "rev-parse", "HEAD")[1],
            after_branch=base,
            hint=f"left on {base}; this checkout's contents are read "
            f"on the integration branch, not as a pull request",
        )
    here = current_branch(repo)
    if here == branch:
        return dict(
            info,
            ok=True,
            action="already-on-phase-branch",
            after_branch=branch,
            hint="the phase is already on its own branch",
        )
    # The remote is read from the base branch, before anything is switched: a
    # branch that does not exist yet has no upstream of its own, so asking this
    # checkout would answer about the wrong branch.
    _base_ref, remote, _rb, why = resolve_upstream(repo)
    if why or not remote:
        rc_r, remote, _ = git(repo, "config", "--get", f"branch.{base}.remote")
        if rc_r != 0 or not remote:
            return degraded(
                info,
                (why or "no remote is configured")
                + "; the branch cut is retried when the remote returns",
            )
    # The base is checked before the current branch, so a missing integration
    # branch is reported as itself rather than as "you are on the wrong
    # branch", which is what the reverse order would say.
    rc, _, err = git(repo, "rev-parse", "--verify", base)
    if rc != 0:
        return degraded(
            info,
            f"base branch {base!r} does not exist; the branch cut is retried next tick",
        )
    if here and here != base:
        # Any branch is a place the launch may leave from when the tree is
        # clean: the phase branch is cut from the base's commit, so nothing
        # uncommitted can be reinterpreted against the wrong tree. A dirty tree
        # is the narrow lost-work stop, on the integration branch or any other.
        carried = dirty_paths(repo)
        if carried:
            return blocked(
                info,
                f"checkout is on {here!r} with {len(carried)} "
                f"uncommitted path(s), and the phase branch is cut from "
                f"{base!r}",
                dirty=carried,
                hint="commit or stash the work, or move the checkout to the "
                f"base branch {base!r} yourself, then rerun; nothing "
                "was switched",
            )
    exists_rc, _, _ = git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    if exists_rc == 0:
        # The branch is left over from an earlier run of this same phase. Its
        # commits are the phase's own history, so move onto it rather than
        # creating a second one; the overlap gate decides whether the two tips
        # can be joined.
        rc, out, err = git(repo, "checkout", branch)
        if rc != 0:
            return degraded(
                info,
                f"{git_failure(f'check out of {branch!r}', rc, err or out)}; "
                "the next tick retries",
            )
        return dict(
            info,
            ok=True,
            action="reused-phase-branch",
            after=git(repo, "rev-parse", "HEAD")[1],
            after_branch=branch,
            hint=f"resumed the existing {branch}",
        )
    rc, out, err = git(repo, "checkout", "-b", branch, base)
    if rc != 0:
        return degraded(
            info,
            f"{git_failure(f'creation of {branch!r}', rc, err or out)}; "
            "the next tick retries",
        )
    # Publish the new branch and give it an upstream straight away. Two
    # reasons, both load-bearing: the sync helper refuses a branch with no
    # upstream, and a branch with no remote copy is the state the phase branch
    # exists to end. The push is at the same commit as the integration branch,
    # so it publishes no unreviewed work.
    rc, out, err = git(repo, "push", "-q", "-u", remote, branch)
    if rc != 0:
        return degraded(
            info,
            f"{git_failure(f'publish of {branch!r} to {remote}', rc, err or out)}; "
            "the phase continues on it and the next tick retries the push",
        )
    return dict(
        info,
        ok=True,
        action="created-phase-branch",
        after=git(repo, "rev-parse", "HEAD")[1],
        after_branch=branch,
        carried=dirty_paths(repo),
        hint=f"created {branch} from {base} and published it, "
        f"carrying the phase's uncommitted work",
    )


def branch_phase_checkouts(
    root: str | Path, branch: str, base: str | None = None
) -> list[dict[str, Any]]:
    """Put the public checkout on ``branch`` and leave the private one alone.

    The two checkouts are reviewed in two different ways, so they are branched
    in two different ways. The public checkout holds a phase's product code,
    which is reviewed as a pull request, so it gets a real phase branch. The
    private checkout holds run state and phase notes, which the operator reads
    on the integration branch, so it is left there and published by pushing
    that branch.

    The private checkout is the one that stays. It is named here rather than
    inlined at the call site because the two are easy to swap and the swap is
    silent: both checkouts report success either way, so a launch onto the
    integration branch looks exactly like a correct one until a phase writes
    unreviewed code straight onto it.

    ``base`` is the commit the phase branch starts at. It defaults to the
    integration branch, and the caller passes the open predecessor's branch
    instead when there is one, so the phase is built on the code the previous
    phase wrote. It applies to the public checkout only.

    The private checkout never gets it. That checkout has no phase branches at
    all: run state is read by the operator on the integration branch and
    published by pushing it, so a predecessor's branch is not a branch it can
    ever sit on. Passing one would fail its own ``here != base`` check and stop
    every launch, which is the kind of failure that reads as a broken pipeline
    rather than as a wiring mistake.

    Returns one result dict per checkout, public first.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    targets: list[Path] = [root] + (
        [private] if is_git_repo(private) and private else []
    )
    return [
        ensure_phase_branch(
            r,
            branch,
            base=(None if r == private else base),
            integration_only=(r == private),
        )
        for r in targets
    ]
