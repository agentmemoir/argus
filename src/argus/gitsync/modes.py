# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The published and tidy modes, and the exit code each outcome earns."""

import re
from pathlib import Path
from typing import Any

from argus.conventions import board_repo, runs_dir
from argus.gitsync.fence import phase_fence
from argus.gitsync.git import is_git_repo
from argus.gitsync.group_publication import (
    _group_all_published,
    _member_published,
)
from argus.gitsync.group_review import _group_pending, mark_group_pull_request_ready
from argus.gitsync.publish import mark_phase_published
from argus.gitsync.receipts import _phase_run_entries, phase_receipt
from argus.gitsync.report import summarize
from argus.gitsync.tidy import return_to_integration, tidy_published_checkout


def result_exit_code(out: dict[str, Any]) -> int:
    if out.get("state") in {"COORDINATION_UNAVAILABLE", "CONFIG_ERROR"}:
        return 2
    return 0 if out.get("ok") else 1


def _ready_one(
    root: Path, phase: int, receipt: dict[str, Any]
) -> dict[str, Any] | None:
    """Try to ready one phase's group pull request, and say what happened.

    ``None`` when the receipt names no group or no pull request: there is
    nothing here to ready, and saying so would fill every tick's log with the
    phases that never had a group.

    The once-only guard is GitHub's own ready history, read inside
    ``mark_group_pull_request_ready``: a pull request with any ready event is
    skipped, so a person who re-drafted it is not overridden by a later tick,
    and a retry that finds no ready event and green checks takes the transition
    exactly once. There is no local ready record on this path and none is
    needed: the tick is the retry, so a tick that could not ready the pull
    request simply tries again next tick.
    """
    members = list(receipt.get("group") or [])
    pull_request = receipt.get("pull_request")
    if not members or not pull_request:
        return None
    pending = _group_pending(root, members, phase)
    if pending:
        return {
            "ok": True,
            "phase": f"{phase:06d}",
            "state": "WAITING",
            "pull_request": pull_request,
            "pending": pending,
            "detail": f"{len(pending)} member(s) of this group have not published",
        }
    try:
        ready = mark_group_pull_request_ready(
            root,
            phase,
            members,
            pending,
            pull_request,
        )
    except Exception as exc:  # noqa: BLE001 - a ready fault must not stop the sweep
        return {
            "phase": f"{phase:06d}",
            "state": "HELD",
            "pull_request": pull_request,
            "detail": f"the group ready could not run: {exc}",
        }
    if ready is None:
        return None
    return dict(ready, state="READY" if ready.get("ok") else "HELD")


def mode_ready(root: str | Path) -> dict[str, Any]:
    """Sweep every group receipt once and ready its pull request when it can.

    The last member of a reservation group makes the group's one pull request
    ready, but only once every check on it reads pass, and the check run that
    follows a push has almost always not finished when the publish happens. That
    first attempt therefore usually finds the checks still running. This mode is
    the retry: it runs on every driver tick and looks at every phase's receipt,
    whatever state that receipt is in, so a group whose checks finish later than
    its publish tick is still readied. It replaces the per-phase ``published``
    retry, which only ran while a receipt said ``awaiting_review`` and so gave
    the checks one tick to finish.

    It is a sweep, not a fence, and it writes nothing at all. It never touches a
    receipt, never changes the published or merge verdict, never commits run
    state, and never holds a claim: the ready transition is guarded by GitHub's
    own ready/draft timeline, which is what makes running it without a claim
    safe. Requiring a live claim would mean the normal end state — the merge
    done, the claim released, the receipt settled — is the one state in which
    the retry cannot run.
    """
    root = Path(root).resolve()
    results: list[dict[str, Any]] = []
    for number, _entry in _phase_run_entries(root):
        receipt = phase_receipt(root, number)
        if not isinstance(receipt, dict):
            continue
        one = _ready_one(root, number, receipt)
        if one is not None:
            results.append(one)
    ok = all(one.get("state") != "HELD" for one in results)
    states = {str(one.get("state")) for one in results}
    if "READY" in states:
        state = "READY"
    elif "HELD" in states:
        state = "HELD"
    elif states:
        state = "WAITING"
    else:
        state = "IDLE"
    return {"ok": ok, "mode": "ready", "state": state, "results": results}


def mode_published(root: str | Path, phase: int | str) -> dict[str, Any]:
    """Confirm a human merged the phase, mark it published, and tidy up.

    Order matters. The board fence is read before anything is written: a
    machine whose claim expired, was released, or was taken over must not mark
    the phase published, and a board that cannot answer fails closed, so the
    write is refused and the next tick retries. The receipt is only written
    after the merge is verified, and
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
    refusal = phase_fence(root, phase, "published")
    if refusal is not None:
        return refusal
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


def mode_tidy(root: str | Path, phase: int | str | None = None) -> dict[str, Any]:
    """Return a checkout sitting on a published phase branch to master.

    ``phase`` is optional because tidy has two callers. The pre-selection call
    in ``lib-driver-select.sh`` runs before a phase is selected, so it has no
    phase to name and stays unfenced; it is read-only checking in the sense that
    matters, because it refuses a dirty tree rather than moving a checkout that
    would lose work. The call from ``_drive_cut_branch`` knows the phase and
    passes it, and then the board fence is read before either checkout moves: a
    phase branch this machine no longer owns must not be cut away under an owner
    that still needs it.
    """
    root = Path(root).resolve()
    if phase is not None:
        refusal = phase_fence(root, phase, "tidy")
        if refusal is not None:
            return refusal
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
