# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Whether a phase already sits on the integration branch, and which predecessor is still open."""

from pathlib import Path
from typing import Any, cast

from argus.gitsync.gh import gh_pull_request_state
from argus.gitsync.git import integration_branch, is_git_repo
from argus.gitsync.receipts import (
    _head_carried_locally,
    _phase_run_entries,
    head_tree_on_integration,
    phase_receipt,
    pull_request_ref,
)


def phase_work_on_integration(
    root: str | Path, phase: int, receipt: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Whether this phase's code is already on the integration branch.

    One derived fact, asked one way, because two places need this answer and
    they were asking it differently: the merge check decided it from the
    receipt's recorded state, and the base-branch chooser decided it from the
    same receipt. A phase whose pull request a human had already merged left the
    receipt stale, so both places kept believing the phase was open — and the
    next phase was cut from a branch whose content was already on master, which
    the branch guard then refused because the checkout was on master. The line
    retried that every tick and never recovered.

    The answer, in order:

    1. A receipt already saying ``published`` is settled and needs no work.
    2. GitHub, asked whether the pull request is ``MERGED``. The reference is
       parsed from the receipt's URL when the receipt predates the recorded
       reference, so an old receipt is still askable.
    3. The commit-id test, for a merge performed outside GitHub.
    4. The content test, for a squash merge seen without GitHub.

    Step 4 is a *code-is-already-there* proof, not a *this-pull-request-was-
    merged* proof, and the difference matters. It may be satisfied by a change
    that reached the integration branch by some other route. That is why it is
    safe for choosing a base branch — where the only question is which tree
    already holds the code — and why ``mark_phase_published`` does not use it.
    A receipt records that a human merged a specific pull request, and only
    GitHub or that pull request's own commit may say so.

    Returns a dict with ``on_integration`` (bool), ``confirmed_by`` and
    ``reason``.
    """
    root = Path(root).resolve()
    info: dict[str, Any] = {
        "phase": f"{int(phase):06d}",
        "on_integration": False,
        "confirmed_by": None,
        "reason": "no publication receipt",
    }
    receipt = receipt if receipt is not None else phase_receipt(root, phase)
    if not receipt:
        return info
    if receipt.get("state") == "published":
        info.update(
            on_integration=True,
            confirmed_by="receipt",
            reason="the receipt already records the merge",
        )
        return info
    integration = receipt.get("integration_branch") or integration_branch(root)
    head = receipt.get("public_head")
    # The parsed reference is used when the receipt has one, and derived from the
    # URL when it does not. That second path is the fix for a receipt written
    # before the reference was recorded: it names the pull request in plain
    # sight, and the old code simply never asked about it.
    ref: dict[str, Any] = {}
    candidate = receipt.get("pull_request_ref")
    if isinstance(candidate, dict):
        ref = cast("dict[str, Any]", candidate)
    if not ref.get("number"):
        ref = pull_request_ref(receipt.get("pull_request")) or {}
    github = {"state": "not-asked", "detail": "no pull request reference"}
    if ref.get("number"):
        state, detail = gh_pull_request_state(
            ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
        )
        github = {"state": state, "detail": detail}
        if state == "merged":
            info.update(
                on_integration=True,
                confirmed_by="github",
                reason=f"GitHub reports pull request {ref['number']} merged",
            )
            info["github"] = github
            return info
    info["github"] = github
    if head and is_git_repo(root):
        if _head_carried_locally(root, head, (integration,)):
            info.update(
                on_integration=True,
                confirmed_by="commit-id",
                reason=f"the published head {head[:12]} is on {integration}",
            )
            return info
        if head_tree_on_integration(root, head, integration):
            info.update(
                on_integration=True,
                confirmed_by="content",
                reason=f"the published content {head[:12]} is on "
                f"{integration} under another commit",
            )
            return info
    info["reason"] = "nothing confirms the phase was merged"
    return info


def awaiting_review(repo: str | Path, number: int) -> dict[str, Any] | None:
    """The branch and pull request a phase is waiting on, or None.

    A phase is waiting for a human when its receipt says so. The receipt is
    written by finalize, which pushes the branch and opens the pull request but
    never merges it, so the phase is not done and the line waits.

    This reads the receipt's recorded state and nothing else. It is what the
    driver builds its merge-check list from, so it must keep reporting a phase
    whose receipt still says ``awaiting_review`` even when the code is already
    on master: that is precisely the receipt the merge check exists to repair,
    and a repaired receipt needs GitHub or its own commit, not an inference
    about content. ``open_predecessor`` is where the content proof belongs.
    """
    receipt = phase_receipt(repo, number)
    if not receipt or receipt.get("state") != "awaiting_review":
        return None
    return {
        "branch": receipt.get("branch"),
        "pull_request": receipt.get("pull_request"),
        "head": receipt.get("public_head"),
    }


def open_predecessor(root: str | Path, phase: int | str) -> dict[str, Any] | None:
    """The highest earlier phase whose code is not yet on master, or None.

    A phase that is waiting for review is finished work. The next phase must be
    built on top of it, not beside it, or the next phase's agent works from a
    tree that does not contain the code it depends on and fails on a symbol that
    does not exist. The earlier phase is the right base because it is the branch
    carrying that code.

    A phase whose code is *already on* the integration branch is not a
    predecessor, whatever its receipt still says. This is where the content
    proof is allowed, because the question here is only which tree already
    carries the code — and stacking the next phase on a branch whose content is
    already on master is what made the branch guard refuse, on every tick, for
    ever. The human merged the pull request; the receipt was never updated; and
    the line had no way to notice.

    Scanning the run directories rather than the roadmap is deliberate. The
    roadmap lists every phase the operator has written, including ones that have
    never run; only a receipt proves a phase reached review. A phase whose
    receipt is unreadable is skipped, matching ``phase_receipt``, because an
    unreadable receipt must never be treated as a base branch.

    Returns ``{"phase", "branch", "pull_request", "head"}`` or None.
    """
    root = Path(root).resolve()
    best: dict[str, Any] | None = None
    for number, _entry in _phase_run_entries(root):
        if number >= int(phase):
            continue
        review = awaiting_review(root, number)
        if not review or not review.get("branch"):
            continue
        if phase_work_on_integration(root, number)["on_integration"]:
            continue
        if best is None or number > best["phase"]:
            best = {
                "phase": number,
                "branch": review["branch"],
                "pull_request": review.get("pull_request"),
                "head": review.get("head"),
            }
    return best
