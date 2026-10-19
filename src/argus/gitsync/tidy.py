# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Returning a checkout to the integration branch once its phase is published."""

from pathlib import Path
from typing import Any

from argus.gitsync.git import (
    blocked,
    current_branch,
    degraded,
    git,
    integration_branch,
    is_git_repo,
)
from argus.gitsync.group_publication import _group_all_published
from argus.gitsync.receipts import receipt_for_branch


def tidy_published_checkout(
    repo: str | Path, base: str | None = None
) -> dict[str, Any]:
    """Return the checkout to the integration branch if its phase is published.

    This exists because of a stall the change that removed the line's review
    wait introduced, and the mechanism that removed made it possible.

    ``mark_phase_published`` writes the receipt, then ``return_to_integration``
    moves the checkout. When the second fails — a stray untracked run log is
    enough — the receipt already says ``published``, so the selector no longer
    reports the phase as awaiting review, so nothing ever retried the move, so
    the checkout stayed on a dead branch and every later phase refused to launch
    on it. The old design retried forever, because stopping at the review wait
    meant the driver re-ran the merge check on every tick. Removing that stop
    removed the only retry that existed.

    So the move is no longer a side effect of confirming a merge. It is its own
    idempotent step, derived from the receipt, run before branching on every
    tick. Whichever of the two effects lands first, the other one still happens.

    Fail-closed in the same way ``return_to_integration`` is: a dirty tree, a
    detached HEAD, or a missing integration branch stops and reports rather than
    switching. A checkout sitting on a phase that is still ``awaiting_review`` is
    deliberately left alone — that phase is not done, and moving would put it on
    a branch the pipeline is not reviewing.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    here = current_branch(repo)
    info: dict[str, Any] = {
        "repo": str(repo),
        "base": base,
        "branch": here or "(detached)",
    }
    if not is_git_repo(repo):
        return degraded(info, "not a git working tree; the tidy is retried next tick")
    if not here or here == base:
        return dict(
            info,
            ok=True,
            action="nothing-to-tidy",
            hint="the checkout is not on an unreviewed phase branch",
        )
    receipt = receipt_for_branch(repo, here)
    if not receipt:
        return dict(
            info,
            ok=True,
            action="no-receipt-for-branch",
            hint=f"{here} has no publication receipt; leaving it alone",
        )
    # A branch shared by a reservation group answers for the group, not for the
    # one member whose receipt happened to be found first. The checkout may only
    # leave while every member is published, because the members still to run
    # need the branch the others published on.
    members = list(receipt.get("group") or [])
    if members:
        if not _group_all_published(repo, members):
            return dict(
                info,
                ok=True,
                action="group-not-yet-published",
                group=members,
                hint=f"{here} carries a reservation group; the checkout "
                "stays on it until every member is published",
            )
        return return_to_integration(repo, base=base)
    state = receipt.get("state")
    if state != "published":
        return dict(
            info,
            ok=True,
            action="phase-not-yet-published",
            phase=receipt.get("phase"),
            receipt_state=state,
            hint=f"{here} belongs to phase {receipt.get('phase')}, "
            f"still {state}; the checkout stays put",
        )
    return return_to_integration(repo, base=base)


def return_to_integration(repo: str | Path, base: str | None = None) -> dict[str, Any]:
    """Put a checkout back on the integration branch once its phase is merged.

    Called only after ``mark_phase_published`` has confirmed the merge is real.
    A phase branch that is never left is a slow drift: the checkout stays on
    branch after branch, and the next sync re-bases from whatever it finds
    rather than from a branch someone chose.

    Fail-closed in the same way ``ensure_phase_branch`` is. A dirty working
    tree is the one case where switching branches could lose work, so that is a
    stop and not a forced checkout. A clean tree on the integration branch
    already is a no-op, and a missing integration branch is a stop rather than
    an invented starting point.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info: dict[str, Any] = {"repo": str(repo), "base": base}
    if not is_git_repo(repo):
        return degraded(info, "not a git working tree; the tidy is retried next tick")
    rc, dirty, _ = git(repo, "status", "--porcelain")
    if rc != 0:
        return degraded(info, "cannot read the working tree status; retrying next tick")
    if dirty.strip():
        return blocked(
            info,
            "the working tree has uncommitted changes",
            hint="commit, stash, or discard them by hand; switching branches "
            "with uncommitted work is how that work gets lost",
        )
    rc, _, _ = git(repo, "rev-parse", "--verify", base)
    if rc != 0:
        return degraded(
            info,
            f"integration branch {base!r} does not exist; the tidy is retried",
        )
    here = current_branch(repo)
    if here == base:
        return dict(
            info,
            ok=True,
            action="already-on-integration-branch",
            hint=f"already on {base}",
        )
    if not here:
        return degraded(
            info,
            "the current branch is not readable; the tidy is retried next tick",
        )
    rc, _, err = git(repo, "checkout", base)
    if rc != 0:
        return degraded(
            info, f"could not check out {base!r}: {err.strip()}; retrying next tick"
        )
    return dict(
        info,
        ok=True,
        action="returned-to-integration-branch",
        before=here,
        after=base,
        hint=f"left {here} and returned to {base}",
    )
