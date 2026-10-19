# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Record a phase as published once its commit, push, and pull request succeeded."""

import json
from pathlib import Path
from typing import Any

from argus.conventions import phase_run_dir
from argus.gitsync.git import blocked
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.runstate import (
    _commit_and_push_run_state,
    _phase_run_pathspec,
    runstate_repo,
)


def _commit_phase_receipt(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Commit and push one phase's run directory in the private checkout.

    ``mark_phase_published`` rewrites a phase's receipt from ``awaiting_review``
    to ``published`` once its commit, push, and pull request succeeded. Whoever
    rewrites a receipt must also commit it. An uncommitted receipt is dirt in the
    private checkout, and the *next* phase's ``publish_phase`` refuses to publish
    on top of another phase's run artifact, so a finished phase blocks the line.
    That is the defect that halted phase 100876: the merge check marked two
    phases published, left both receipts modified, and the running phase's
    finalize then refused with ``working tree is dirty after phase cleanup``.

    Deliberately narrower than ``commit_leftover_run_state``. That helper also
    refuses when the *public* checkout is dirty, and the public checkout is
    normally mid-work while another phase runs — which is exactly when the
    receipt is settled. This commits only this phase's own run directory, in the
    private repo, and pushes it, so a running phase's uncommitted work cannot
    block a finished phase's receipt. The phase scope is the same guarantee
    ``commit_leftover_run_state`` makes: a phase never carries another phase's
    run state.

    The receipt is already committed when only the push fails, and that is left
    alone because the working tree is clean at that point. A commit that fails
    leaves the receipt dirty and is reported by ``mark_phase_published``, which
    continues rather than restoring the bytes or blocking the run.
    """
    repo = runstate_repo(root, phase)
    if repo is None:
        return blocked(
            {"repo": str(root)},
            "no run-state checkout to commit the publication receipt to",
        )
    return _commit_and_push_run_state(
        repo,
        f"pipeline: publish completion receipt for phase {int(phase):06d}",
        pathspec=_phase_run_pathspec(phase, root),
    )


def mark_phase_published(
    root: str | Path,
    phase: int | str,
) -> dict[str, Any]:
    """Record that a phase's pull request is published.

    The phase is marked once its commit, push, and pull request succeeded. No
    merge is required and no local commit-id or receipt check gates it: a merge
    is external, and a missing proof must not stop the run. A phase with no
    receipt has nothing to mark, so it returns without writing. The rewritten
    receipt is committed, and a commit or push that fails is reported rather
    than raised.
    """
    root = Path(root).resolve()
    info: dict[str, Any] = {"repo": str(root), "phase": f"{int(phase):06d}"}
    receipt = phase_receipt(root, int(phase))
    if not receipt:
        return dict(
            info,
            ok=True,
            action="no-receipt",
            hint="no publication receipt to mark; nothing to record",
        )
    if receipt.get("state") == "published":
        return dict(
            info,
            ok=True,
            action="already-published",
            hint="this phase is already recorded as published",
        )
    if not receipt.get("public_head") and not receipt.get("missing_heads"):
        # A receipt with no pushed head and no recorded missing head is not a
        # publish receipt: `_write_publication_receipt` always records one or the
        # other. Leave it for a later tick; there is nothing to mark.
        return dict(
            info,
            ok=False,
            action="no-publish-evidence",
            blockers=["the receipt records no pushed head; nothing to mark"],
            hint="the phase keeps waiting until its publish records a head",
        )
    path = phase_run_dir(root, int(phase)) / "publication.json"
    data = dict(receipt)
    data["state"] = "published"
    data["public_commit"] = receipt.get("public_head")
    data["private_commit"] = receipt.get("private_head")
    data["merged_at"] = _phase_reservations_now()
    data["merge_confirmed_by"] = {"public": "recorded", "private": "recorded"}
    data.pop("engine_written_at_publish", None)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    # The write is not enough on its own: an uncommitted receipt is dirt in the
    # private checkout. Commit it; a failed commit or push is external and must
    # not stop the run, so it is reported instead.
    committed = _commit_phase_receipt(root, phase)
    if not committed.get("ok"):
        info["receipt_commit"] = committed.get("blockers") or committed.get("summary")
    return dict(
        info,
        ok=True,
        action="marked-published",
        public_commit=data["public_commit"],
        private_commit=data["private_commit"],
        confirmed_by=data["merge_confirmed_by"],
        receipt_committed=committed.get("action"),
        hint="the phase's commits are on its branch and its pull request is open",
    )


def _phase_reservations_now() -> Any:
    from argus.board.reservations import _now

    return _now()
