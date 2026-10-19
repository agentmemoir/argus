# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The published and tidy modes, and the exit code each outcome earns."""

import re
from pathlib import Path
from typing import Any

from argus.conventions import board_repo, runs_dir
from argus.gitsync.git import is_git_repo, summarize
from argus.gitsync.group_publication import _group_all_published, _member_published
from argus.gitsync.publish import mark_phase_published
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.tidy import return_to_integration, tidy_published_checkout


def result_exit_code(out: dict[str, Any]) -> int:
    if out.get("state") in {"COORDINATION_UNAVAILABLE", "CONFIG_ERROR"}:
        return 2
    return 0 if out.get("ok") else 1


def mode_published(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Confirm a human merged the phase, mark it published, and tidy up.

    Order matters. The receipt is only written after the merge is verified, and
    the checkout only moves after the receipt says the phase is done. Doing
    either first would let a phase look finished while its code is still
    unreviewed.

    The move is best-effort and reported separately from the merge, because the
    two can succeed and fail independently. When they do, ``tidy_published_
    checkout`` completes the move on a later tick; it used to be that this
    path's own retry was the only one, and removing the line's review wait
    removed it.
    """
    root = Path(root).resolve()
    marked = mark_phase_published(root, phase)
    if marked.get("action") == "no-publish-evidence":
        # There is nothing to mark yet. The phase keeps waiting; the next tick
        # retries when its publish records a head. Never report it published.
        return {
            "ok": True,
            "mode": "published",
            "state": "RETRY",
            "repos": [marked],
            "summary": summarize([marked], "published"),
        }
    if not marked["ok"]:
        return {
            "ok": False,
            "mode": "published",
            "state": "BLOCKED",
            "repos": [marked],
            "summary": summarize([marked], "published"),
        }
    repos: list[dict[str, Any]] = [marked]
    # A member of a group does not take the checkout with it. One branch and one
    # pull request cover the whole group, so the members that have not run yet
    # still need that branch; moving the checkout to the integration branch here
    # would take it out from under the next member's launch.
    receipt = phase_receipt(root, int(phase))
    members = list(receipt.get("group") or []) if isinstance(receipt, dict) else []
    if members and not _group_all_published(root, members):
        pending = [m for m in members if not _member_published(root, m)]
        repos.append(
            {
                "repo": str(root),
                "ok": True,
                "action": "group-still-open",
                "group": members,
                "pending": pending,
                "hint": f"{len(pending)} member(s) of this reservation "
                "group are not published; the checkout stays on "
                "the group branch",
            }
        )
        return {
            "ok": True,
            "mode": "published",
            "state": "PUBLISHED-GROUP-PENDING",
            "repos": repos,
            "summary": summarize(repos, "published"),
        }
    repos.append(return_to_integration(root))
    # The private checkout stays on the integration branch by design, so only
    # the public one can drift. A stop there is still a stop: report it rather
    # than claiming the phase is tidied when the checkout is not.
    ok = all(r["ok"] for r in repos)
    state = "PUBLISHED" if ok else "PUBLISHED-TIDY-PENDING"
    return {
        "ok": ok,
        "mode": "published",
        "state": state,
        "repos": repos,
        "summary": summarize(repos, "published"),
    }


def mode_tidy(root: str | Path) -> dict[str, Any]:
    """Return a checkout sitting on a published phase branch to master."""
    root = Path(root).resolve()
    repos: list[dict[str, Any]] = [tidy_published_checkout(root)]
    nested = board_repo(root)
    if nested is not None and is_git_repo(nested):
        repos.append(tidy_published_checkout(nested))
    ok = all(r["ok"] for r in repos)
    return {
        "ok": ok,
        "mode": "tidy",
        "repos": repos,
        "summary": summarize(repos, "tidy"),
    }


def _run_phase_numbers(root: str | Path) -> list[int]:
    """Every phase number with a run directory under ``runs/``, lowest first."""
    numbers: list[int] = []
    for path in sorted(runs_dir(root).glob("phase-*")):
        match = re.fullmatch(r"phase-(\d+)", path.name)
        if match and path.is_dir():
            numbers.append(int(match.group(1)))
    return numbers


__all__ = ["_run_phase_numbers"]
