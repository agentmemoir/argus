# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Marking a phase published once a human has merged its pull request."""

import json
from pathlib import Path
from typing import Any, cast

from argus.conventions import board_repo, phase_run_dir
from argus.gitsync.gh import gh_pull_request_state
from argus.gitsync.git import (
    FETCH_TIMEOUT_S,
    blocked,
    dirty_paths,
    git,
    integration_branch,
    is_git_repo,
)
from argus.gitsync.receipts import (
    _head_carried_locally,
    phase_receipt,
    pull_request_ref,
)
from argus.gitsync.runstate import _commit_and_push_run_state, _phase_run_pathspec


def _commit_phase_receipt(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Commit and push one phase's run directory in the private checkout.

    ``mark_phase_published`` rewrites a merged phase's receipt from
    ``awaiting_review`` to ``published``. Whoever rewrites a receipt must also
    commit it. An uncommitted receipt is dirt in the private checkout, and the
    *next* phase's ``publish_phase`` refuses to publish on top of another
    phase's run artifact, so a finished phase blocks the line. That is the
    defect that halted phase 100876: the merge check marked two merged phases
    published, left both receipts modified, and the running phase's finalize
    then refused with ``working tree is dirty after phase cleanup``.

    Deliberately narrower than ``commit_leftover_run_state``. That helper also
    refuses when the *public* checkout is dirty, and the public checkout is
    normally mid-work while another phase runs — which is exactly when the
    merge check fires. This commits only this phase's own run directory, in the
    private repo, and pushes it, so a running phase's uncommitted work cannot
    block a finished phase's receipt. The phase scope is the same guarantee
    ``commit_leftover_run_state`` makes: a phase never carries another phase's
    run state.

    The receipt is already committed when only the push fails, and that is left
    alone because the working tree is clean at that point. When the commit
    itself fails the receipt is still dirty and nobody owns it, so
    ``mark_phase_published`` restores the previous bytes; see it for why.
    """
    private = cast("Path", board_repo(root))
    return _commit_and_push_run_state(
        private,
        f"pipeline: publish completion receipt for phase {int(phase):06d}",
        pathspec=_phase_run_pathspec(phase, root),
    )


def mark_phase_published(
    root: str | Path,
    phase: int | str,
    public_commit: str | None = None,
    private_commit: str | None = None,
) -> dict[str, Any]:
    """Record that a human merged a phase's pull request.

    This is the only path from ``awaiting_review`` to ``published``, and it
    refuses unless the merge is real. That is what stops a receipt being edited
    into claiming a merge that did not happen, which is the one outcome a phase
    branch must never produce.

    Two independent confirmations, in this order:

    1. GitHub, asked whether the phase's pull request is ``MERGED``. GitHub
       records the merge itself, and records it identically for a merge commit,
       a squash, and a rebase. This is the check that works for a squash: a
       squash deletes the commit the phase pushed, so the commit-id test below
       reports "not merged" for a phase a human has already merged, and the line
       waits for a merge that already happened. Measured with a five-commit
       phase squash-merged onto the integration branch; the commit-id test
       failed on it and GitHub's answer was the only one that did not.
    2. The commit-id test: the head finalize pushed must be an ancestor of the
       integration branch in each checkout. It still runs whenever GitHub could
       not answer, so a merge performed outside GitHub is still recognised, and
       it still gates the private checkout, which has no pull request because
       the pipeline pushes its own integration branch directly.

    A failed GitHub call is never a merged phase. ``gh_pull_request_state``
    returns three answers rather than two so that "could not ask" and "asked,
    not merged" stay apart, and the unknown case falls through to the commit-id
    test, which fails closed.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    private = cast("Path", board_repo(root))
    info: dict[str, Any] = {"repo": str(root), "phase": f"{int(phase):06d}"}
    receipt = phase_receipt(root, int(phase))
    if not receipt:
        return blocked(info, "no publication receipt for this phase")
    if receipt.get("state") == "published":
        return dict(
            info,
            ok=True,
            action="already-published",
            hint="this phase is already recorded as merged",
        )
    if receipt.get("state") != "awaiting_review":
        return blocked(
            info, f"receipt state is {receipt.get('state')!r}, not awaiting review"
        )
    merged: dict[str, Any] = {}
    confirmed_by: dict[str, str] = {}
    github = {"state": "not-asked", "detail": "no pull request reference"}
    for label, checkout, want in (
        ("public", root, public_commit),
        ("private", private, private_commit),
    ):
        head = receipt.get(f"{label}_head")
        if not head:
            return blocked(info, f"receipt has no {label} head to verify")
        if not is_git_repo(checkout):
            return blocked(info, f"{label} checkout is not a git working tree")
        # The receipt names the branch the pull request targeted, so a renamed
        # integration branch cannot make a real merge look unmerged.
        target = receipt.get("integration_branch") or integration_branch(checkout)
        if label == "public":
            # The recorded reference when the receipt has one, and the reference
            # parsed from the receipt's own URL when it does not. An old receipt
            # names its pull request in plain sight, and asking GitHub about it
            # is the only way a squash-merged phase is ever recognised on a host
            # where the commit-id test cannot see the merge.
            ref: dict[str, Any] = {}
            stored_ref = receipt.get("pull_request_ref")
            if isinstance(stored_ref, dict):
                ref = cast("dict[str, Any]", stored_ref)
            if not ref.get("number"):
                ref = pull_request_ref(receipt.get("pull_request")) or {}
            if ref.get("number"):
                state, detail = gh_pull_request_state(
                    ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
                )
                github = {"state": state, "detail": detail}
                if state == "merged":
                    merged[label] = detail or f"pull-request-{ref['number']}"
                    confirmed_by[label] = "github"
                    continue
        # Local refs are tried before any fetch. The private checkout pushes its
        # own integration branch at publish time, so its head is already an
        # ancestor of the local target and needs no network at all; and for the
        # public checkout the local answer is only ever possible when the merge
        # has not moved, so a fetch there is wasted too. Fetching only when the
        # local answer failed keeps this off the network on every tick where
        # nothing has been merged, which is the overwhelming majority.
        #
        # No content test here, on purpose. This function records that a human
        # merged a specific pull request, and the content of a phase being
        # present on the integration branch does not say that: the same content
        # can arrive by another route. Only GitHub, or that pull request's own
        # commit, may write "published" into a receipt. The content proof is
        # used by ``phase_work_on_integration`` to choose a base branch, which
        # is a question about where code lives rather than about what a human did.
        found = _head_carried_locally(checkout, head, (want, target))
        if found:
            merged[label] = found
            confirmed_by[label] = "commit-id"
        else:
            git(checkout, "fetch", "--prune", "origin", timeout=FETCH_TIMEOUT_S)
            found = _head_carried_locally(checkout, head, (f"origin/{target}",))
            if found:
                merged[label] = found
                confirmed_by[label] = "commit-id"
        if label not in merged:
            # Which check answered is reported, because the two answer for
            # different reasons and an operator reading a stalled line needs to
            # know whether GitHub said no or could not be asked.
            reason = (
                f"the {label} head {head[:12]} is not on the integration "
                "branch, so the pull request has not been merged"
            )
            if label == "public" and github["state"] == "not-merged":
                reason += " (GitHub reports the pull request as not merged)"
            elif label == "public" and github["state"] == "unknown":
                reason += (
                    f" (GitHub could not be asked: {github['detail']}). The "
                    "squash-merge check is degraded, so a squash-merged phase "
                    "will keep waiting until gh works again"
                )
            return blocked(
                info,
                reason,
                github=github,
                hint="merge the pull request, then rerun; a receipt is never "
                "edited into claiming a merge that did not happen",
            )
    path = phase_run_dir(root, int(phase)) / "publication.json"
    data = dict(receipt)
    data["state"] = "published"
    data["public_commit"] = merged["public"]
    data["private_commit"] = merged["private"]
    data["merged_at"] = _phase_reservations_now()
    # Recorded per checkout, because the two are confirmed by different proofs.
    # The private checkout has no pull request at all — the pipeline pushes its
    # own integration branch directly — so its head is always confirmed by the
    # commit-id test. A single value would claim GitHub vouched for a head it
    # was never asked about, which defeats the field's whole purpose.
    data["merge_confirmed_by"] = {
        k: confirmed_by.get(k, "commit-id") for k in ("public", "private")
    }
    data.pop("engine_written_at_publish", None)
    try:
        original = path.read_text()
    except OSError:
        original = None
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    # The write is not enough on its own. An uncommitted receipt is dirt in the
    # private checkout, and the next phase's publish refuses to run over it, so
    # the phase that rewrites the receipt commits it in the same step.
    committed = _commit_phase_receipt(root, phase)
    if not committed.get("ok"):
        receipt_rel = str(path.relative_to(private))
        if original is not None and receipt_rel in dirty_paths(private):
            # The commit did not land, so the receipt is still dirty and no
            # later tick will retry it: a receipt that reads published leaves
            # the awaiting-review selection, so this phase's merge check never
            # runs again. Put the previous bytes back, so the phase stays
            # awaiting_review, the next tick retries the whole step, and the
            # tree the next phase sees is clean.
            path.write_text(original)
            return blocked(
                info,
                f"could not commit the published receipt: {committed.get('blockers')}",
                hint="the receipt was restored to awaiting_review; the merge "
                "check retries on the next tick",
            )
        return blocked(
            info,
            "the published receipt was committed but the push failed: "
            f"{committed.get('blockers')}",
            hint="the working tree is clean and the receipt is committed; the "
            "next sync pushes it",
        )
    return dict(
        info,
        ok=True,
        action="marked-published",
        public_commit=merged["public"],
        private_commit=merged["private"],
        confirmed_by=data["merge_confirmed_by"],
        receipt_committed=committed.get("action"),
        hint="the phase's commits are on the integration branch",
    )


def _phase_reservations_now() -> Any:
    from argus.board.reservations import _now

    return _now()
