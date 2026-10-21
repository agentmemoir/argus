# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group's one pull request: reuse it while it is open, ready it when the group finishes."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from argus.conventions import phase_run_dir
from argus.gitsync.gh import gh_mark_pull_request_ready, gh_pull_request_open
from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.receipts import _phase_run_entries, phase_receipt, pull_request_ref


def _sibling_pull_request_reusable(receipt: dict[str, Any]) -> bool:
    """Whether a sibling's recorded pull request can still cover the group.

    Two questions, in order. A receipt already reading ``published`` is settled:
    its member's pull request merged, so that pull request cannot carry the
    member being published now. Otherwise GitHub is asked whether the recorded
    pull request is still open.

    When GitHub cannot answer the pull request is reused. That is deliberate and
    it is the safe direction here: the merge check already degrades without
    GitHub, and dropping the record would leave every later member of the group
    naming no pull request at all, which is worse than naming the one the group
    recorded. A pull request GitHub positively reports closed or merged is never
    reused, which is the case that recorded a merged pull request in a member's
    receipt.
    """
    if receipt.get("state") == "published":
        return False
    ref = pull_request_ref(receipt.get("pull_request"))
    if not ref:
        return True
    is_open, _detail = gh_pull_request_open(
        ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
    )
    return is_open is not False


def _group_pull_request(
    root: str | Path, group: Iterable[Any], branch: str | None, phase: int
) -> Any:
    """The pull request another member of this group still has open.

    One branch and one pull request cover a group, so every member's receipt
    names the pull request the group already has. The join key is the group
    branch, and the member list is checked with it: once one branch carries
    several receipts, the branch name alone no longer names one phase. The member
    being published is skipped, so republishing it cannot answer with its own
    earlier record.

    A sibling's recorded pull request is reused only while it still covers the
    branch. After a member's pull request merges, the branch is reset onto the
    integration branch and pushed again, and GitHub opens a second pull request
    on that same branch, so the merged one no longer carries the phase being
    published. Recording it would put a merged pull request in a new member's
    receipt while its head is only on the open one.
    """
    if not branch:
        return None
    wanted = {f"{int(member):06d}" for member in group}
    mine = f"{int(phase):06d}"
    for number, _entry in _phase_run_entries(root):
        if f"{int(number):06d}" == mine:
            continue
        receipt = phase_receipt(root, number)
        if not isinstance(receipt, dict) or receipt.get("branch") != branch:
            continue
        recorded: list[Any] = receipt.get("group") or []
        if {f"{int(member):06d}" for member in recorded} != wanted:
            continue
        if _sibling_pull_request_reusable(receipt):
            return receipt.get("pull_request")
    return None


def _group_pending(root: str | Path, members: Iterable[Any], phase: int) -> list[str]:
    """The group members other than ``phase`` that have not published yet.

    A member has published once it has a publication receipt, whatever the
    receipt's state: the receipt is written when the member's commits reach the
    group branch, and merging is a later, separate step. The list is in
    declaration order, which is the order the members run in.
    """
    mine = f"{int(phase):06d}"
    pending: list[str] = []
    for member in members:
        key = f"{int(member):06d}"
        if key != mine and phase_receipt(root, int(member)) is None:
            pending.append(key)
    return pending


def _group_ready_marker(root: str | Path, phase: int) -> Path:
    """The ready-once record for a group's shared pull request."""
    return phase_run_dir(root, int(phase)) / "group-pr-ready.json"


def mark_group_pull_request_ready(
    root: str | Path,
    phase: int,
    pending: Iterable[Any],
    pull_request: str | None,
) -> dict[str, Any] | None:
    """The one place a group's shared pull request is marked ready for review.

    A group keeps its one pull request in draft until the last unfinished member
    publishes. Every member runs this and only the last one finds no member left
    to publish, so a shared pull request is made ready exactly once. Returns the
    gh result, or None when there is nothing to do: a member is still to run, no
    pull request is recorded, or its URL cannot name a pull request to act on.

    The transition is gated on the pull request's checks: the ready is recorded
    only when gh reports at least one check and every one reads ``pass``, so a
    pull request that is not yet mergeable stays draft. An empty check report is
    a refusal too: gh returning zero rows is not ``all checks pass``, so a pull
    request with no reported checks stays draft until checks are reported. The
    ready-once record beside the receipt is
    written on success and stands for every later run of this phase, so a human
    who re-drafts the pull request is never overridden.

    A failed transition is reported, never raised. The phase's commits are
    already on the branch and its receipt is written, so making the pull request
    ready is the last step and its failure must not undo the publication. When
    the ready lands but the record write fails, the result says so:
    ``ready_landed`` is true and ``record_failed`` is true alongside ``ok``
    false, so a rerun's re-ready is understood instead of looking like a bare
    refusal.
    """
    if list(pending) or not pull_request:
        return None
    ref = pull_request_ref(pull_request)
    if not ref:
        return None
    marker = _group_ready_marker(root, phase)
    if marker.is_file():
        try:
            recorded = json.loads(marker.read_text())
        except (OSError, ValueError):
            recorded = None
        if (
            isinstance(recorded, dict)
            and cast("dict[str, Any]", recorded).get("pull_request") == pull_request
        ):
            return {
                "ok": True,
                "phase": f"{int(phase):06d}",
                "pull_request": pull_request,
                "detail": "the ready-once record stands; a re-drafted pull request is never re-readied",
                "skipped": True,
            }
    rows, code, detail = gh_pull_request_checks(Path(root), ref["number"])
    # gh returning zero rows is not "all checks pass": an empty report names no
    # passing check, so the pull request stays draft until checks are reported.
    if (
        rows is None
        or code != 0
        or not rows
        or any(str(row.get("bucket") or "") != "pass" for row in rows)
    ):
        return {
            "ok": False,
            "phase": f"{int(phase):06d}",
            "pull_request": pull_request,
            "detail": detail
            or "the checks are not all green; the pull request stays draft",
        }
    ready, detail = gh_mark_pull_request_ready(
        ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
    )
    result = {
        "ok": ready,
        "phase": f"{int(phase):06d}",
        "pull_request": pull_request,
        "detail": detail,
    }
    if ready:
        from argus.board.reservations import _now

        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {"version": 1, "pull_request": pull_request, "ready_at": _now()},
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        except OSError as exc:
            # The ordering matters for a rerun: the ready landed, so the pull
            # request is non-draft, but the ready-once record is missing, so a
            # rerun re-evaluates and re-readies. Reporting both states keeps a
            # rerun's re-ready understood instead of looking like a failure.
            return {
                "ok": False,
                "phase": f"{int(phase):06d}",
                "pull_request": pull_request,
                "detail": f"the ready-once record could not be written: {exc}",
                "ready_landed": True,
                "record_failed": True,
            }
    return result


__all__ = [
    "_group_pending",
    "_group_pull_request",
    "mark_group_pull_request_ready",
]
