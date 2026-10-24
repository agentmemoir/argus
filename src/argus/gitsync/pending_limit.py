# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The pending limit for one pull request's checks: one Needs You notice per head.

A check that stays pending for more than ``PENDING_LIMIT_S`` on one head raises
one notice for that head, and no later tick repeats it for that head. The clock
starts at the first tick that sees a pending check on the head. gh does not
report when a check started, so that tick is the earliest moment Argus can know.
The notice clears when no check is pending any more, or when the head moves.

The state is a small JSON file in the run folder. A damaged file is read as
empty, so one notice may repeat once after it is replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PENDING_LIMIT_S = 30 * 60


def _load(path: Path) -> dict[str, Any]:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return cast("dict[str, Any]", data)


def _save(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _names(rows: list[dict[str, Any]]) -> str:
    """The pending check names, joined on one line, as data from CI."""
    names = [" ".join(str(r.get("name") or "a check").split()) for r in rows]
    return ", ".join(names) if names else "a check"


def step(
    path: Path,
    pull_request: str,
    head: str | None,
    rows: list[dict[str, Any]],
    now: float,
) -> dict[str, str]:
    """Update the pending state for one tick, and return the lines to show.

    ``head`` must be the pull request's head SHA whenever a check is pending. The
    caller reads it only then, and stops the tick when that read fails. The
    result holds ``notice`` (one Needs You line) and ``cleared`` (one log line),
    each only when it applies.
    """
    record = _load(path)
    pending = [r for r in rows if r.get("bucket") == "pending"]
    out: dict[str, str] = {}
    if record.get("open") and (not pending or head != record["open"]):
        reason = "no check is pending" if not pending else "the head moved"
        out["cleared"] = (
            f"Argus cleared the pending-check notice for {pull_request}: {reason}."
        )
        record.pop("open", None)
    if not pending:
        record.pop("head", None)
        record.pop("since", None)
    else:
        if head is None:
            raise ValueError("a pending check needs the head SHA")
        if record.get("head") != head:
            record["head"] = head
            record["since"] = now
        noticed = [str(h) for h in record.get("noticed", [])]
        if now - float(record["since"]) > PENDING_LIMIT_S and head not in noticed:
            noticed.append(head)
            record["noticed"] = noticed
            record["open"] = head
            out["notice"] = (
                f"Needs You. {pull_request} has a check pending for over 30 minutes "
                f"on commit {head[:12]}: {_names(pending)}. Action: look at the stuck "
                "check, or re-run it. The next tick checks again and clears this "
                "notice when no check is pending."
            )
    _save(path, record)
    return out


def noticed(path: Path, head: str | None) -> bool:
    """Whether the one Needs You notice for this head already went out."""
    return head is not None and bool(head in _load(path).get("noticed", []))
