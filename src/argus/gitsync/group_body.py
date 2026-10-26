# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The group pull request's body: labels, the checklist, and the member comment."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from argus.gitsync.gh import (
    gh_comment_pull_request,
    gh_edit_pull_request_body,
    gh_pull_request_body,
)
from argus.gitsync.gh_gate import gh_pull_request_comment_bodies
from argus.gitsync.receipts import phase_receipt, pull_request_ref


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
    "_group_task_labels",
    "_tidy_group_body",
    "_update_body_checklist",
    "update_group_pull_request",
]
