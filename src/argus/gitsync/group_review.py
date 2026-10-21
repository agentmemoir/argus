# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group's one pull request: reuse it while it is open, ready it when the group finishes."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from argus.conventions import phase_run_dir
from argus.gitsync.gh import (
    gh_comment_pull_request,
    gh_edit_pull_request_body,
    gh_mark_pull_request_ready,
    gh_pull_request_body,
    gh_pull_request_open,
)
from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.gh_gate import gh_pull_request_comment_bodies
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
    only when every check gh reports reads ``pass``, so a pull request that is
    not yet mergeable stays draft. The ready-once record beside the receipt is
    written on success and stands for every later run of this phase, so a human
    who re-drafts the pull request is never overridden.

    A failed transition is reported, never raised. The phase's commits are
    already on the branch and its receipt is written, so making the pull request
    ready is the last step and its failure must not undo the publication.
    """
    if list(pending) or not pull_request:
        return None
    ref = pull_request_ref(pull_request)
    if not ref:
        return None
    marker = _group_ready_marker(root, phase)
    if marker.is_file():
        return {
            "ok": True,
            "phase": f"{int(phase):06d}",
            "pull_request": pull_request,
            "detail": "the ready-once record stands; a re-drafted pull request is never re-readied",
            "skipped": True,
        }
    rows, code, detail = gh_pull_request_checks(Path(root), ref["number"])
    if (
        rows is None
        or code != 0
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
        except OSError:
            # The record could not be written. The ready itself landed; a
            # rerun would find gh idempotent, and the gap is reported by the
            # caller's dirt check rather than hidden here.
            pass
    return result


def _normalize_label(label: str) -> str:
    """A checkbox label in comparable form: case, dashes, and spacing unified."""
    import re

    text = label.strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_label(label: str) -> str:
    """A label safe for public text: no phase numbers or long tails."""
    import re

    text = re.sub(r"\b\d{6}\b", "", label)
    text = re.sub(r"\s+", " ", text).strip(" -_:")
    return text[:120] if len(text) > 120 else text or "group item"


def _group_task_labels(
    root: str | Path, members: Iterable[Any]
) -> list[tuple[str, str]]:
    """One public label per group member, in declaration order.

    The label is the phases behaviour slug, never the phase number: the branch
    already carries the slug, so it is public by construction. A member with no
    document falls back to a numbered task, which keeps checkboxes distinct
    without leaking anything.
    """
    from argus.conventions import phase_file_parts, phase_paths
    from argus.gitsync.paths import phases_folder

    keys = [f"{int(member):06d}" for member in members]
    slugs: dict[str, str] = {}
    try:
        folder = phases_folder(root)
        for path in phase_paths(folder):
            parts = phase_file_parts(path)
            if parts is not None and f"{int(parts[0]):06d}" in keys:
                slugs[f"{int(parts[0]):06d}"] = str(parts[1])
    except Exception:  # noqa: BLE001 - a missing phases falls back below
        slugs = {}
    labels: list[tuple[str, str]] = []
    for i, key in enumerate(keys):
        raw = slugs.get(key, "")
        if raw:
            text = _sanitize_label(raw.replace("-", " ").replace("_", " "))
        else:
            text = f"task {i + 1}"
        labels.append((key, text or "group item"))
    return labels


def _update_body_checklist(
    body: str, labels_done: list[tuple[str, bool]]
) -> tuple[str, bool]:
    """Tick known checkboxes in place, appending any missing label.

    Lines already checked stay checked: this never unticks. Returns the new
    body and whether anything changed.
    """
    import re

    wanted = {_normalize_label(label): done for label, done in labels_done}
    seen: set[str] = set()
    changed = False
    out: list[str] = []
    pattern = re.compile(r"^(\s*-\s*\[)( |x|X)(\](\s*)(.+?)(\s*))$")
    for line in body.splitlines():
        match = pattern.match(line)
        if not match:
            out.append(line)
            continue
        norm = _normalize_label(match.group(5))
        if norm not in wanted:
            out.append(line)
            continue
        seen.add(norm)
        if wanted[norm] and match.group(2) == " ":
            out.append(f"{match.group(1)}x]{match.group(4)}{match.group(5).strip()}")
            changed = True
        else:
            out.append(line)
    for label, done in labels_done:
        if _normalize_label(label) not in seen:
            box = "x" if done else " "
            out.append(f"- [{box}] {label}")
            changed = True
    return "\n".join(out) + "\n", changed


def _tidy_group_body(body: str) -> str:
    """Final whitespace tidy before ready: no trailing spaces, no tall gaps."""
    import re

    lines = [line.rstrip() for line in body.split("\n")]
    text = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{4,}", "\n\n\n", text)


def update_group_pull_request(
    root: str | Path, phase: int, members: Iterable[Any], pull_request: str | None
) -> dict[str, Any] | None:
    """Tick this member's checkbox, then post its comment. Never raises.

    Every member runs this on publish, including the first and the last. The
    comment goes out only after the checkbox lands (or was already current),
    so a failed body edit never leaves a ``Completed`` comment beside an open
    box. The last member's edit also tidies the body; ready itself stays in
    ``mark_group_pull_request_ready`` so it still happens exactly once. A
    GitHub failure is reported in the result and never undoes the publication.
    """
    if not pull_request:
        return None
    ref = pull_request_ref(pull_request)
    if not ref:
        return None
    member_list = [f"{int(member):06d}" for member in members]
    labels = _group_task_labels(root, member_list)
    done_keys = {
        key for key in member_list if phase_receipt(root, int(key)) is not None
    }
    labels_done = [(label, key in done_keys) for key, label in labels]
    mine = f"{int(phase):06d}"
    my_label = next((label for key, label in labels if key == mine), "group item")
    receipt = phase_receipt(root, int(phase))
    head = ""
    if isinstance(receipt, dict):
        public_head = str(receipt.get("public_head") or "")
        if public_head:
            head = f" ({public_head[:7]})"
    body, body_detail = gh_pull_request_body(
        ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
    )
    if body is None:
        return {
            "ok": False,
            "phase": mine,
            "pull_request": pull_request,
            "comment_ok": None,
            "comment_detail": "comment skipped: pull request body unreadable",
            "body_ok": None,
            "body_detail": body_detail,
        }
    new_body, changed = _update_body_checklist(body, labels_done)
    pending = [key for key in member_list if key not in done_keys]
    if not pending:
        new_body = _tidy_group_body(new_body)
        changed = changed or new_body != body
    if changed:
        body_ok, body_detail = gh_edit_pull_request_body(
            ref["number"],
            ref.get("owner"),
            ref.get("repo"),
            ref.get("host"),
            new_body,
        )
    else:
        body_ok, body_detail = True, "body already current"
    comment_ok: bool | None = None
    comment_detail = "comment skipped: body update failed"
    if body_ok:
        comment = f"Completed {my_label}.{head}"
        # A publish that runs again for the same head (a retry after a failed
        # step, a rerun of the helper) finds its comment already there. When the
        # comments cannot be read the comment is posted: a repeat is noise, a
        # missing comment is lost.
        posted = gh_pull_request_comment_bodies(
            ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
        ).value
        if posted is not None and comment in {text.strip() for text in posted}:
            comment_ok, comment_detail = True, "comment already posted"
        else:
            comment_ok, comment_detail = gh_comment_pull_request(
                ref["number"],
                ref.get("owner"),
                ref.get("repo"),
                ref.get("host"),
                comment,
            )
    return {
        "ok": bool(comment_ok and body_ok),
        "phase": mine,
        "pull_request": pull_request,
        "comment_ok": comment_ok,
        "comment_detail": comment_detail,
        "body_ok": body_ok,
        "body_detail": body_detail,
    }


__all__ = [
    "_group_pending",
    "_group_pull_request",
    "_group_task_labels",
    "_tidy_group_body",
    "_update_body_checklist",
    "mark_group_pull_request_ready",
    "update_group_pull_request",
]
