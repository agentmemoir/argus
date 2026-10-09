#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue lifecycle actions. Queue only text already checked by the host helper."""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import _bootstrap as _bootstrap  # noqa: F401

from argus import conventions
from argus.board.core import MACHINE_ID_RE
from argus.board.reservations import CoordinationError, ReservationStore
from argus.config.loader import ConfigError
from argus.issues.claims import retry, token
from argus.issues.client import IssueClient, issue_number
from argus.issues.transport import IssuesUnavailable


def announce(client: IssueClient, number: int, machine: str, event: str) -> None:
    if not MACHINE_ID_RE.fullmatch(machine):
        raise IssuesUnavailable("configuration", "machine.id is invalid")
    messages = {
        "released": "released this issue back to the queue.",
        "halted": "halted work on this issue; a human decision is needed.",
        "assignment-failed": "claimed this issue on the board; GitHub assignment failed.",
    }
    if event not in messages:
        raise IssuesUnavailable("configuration", "unknown issue announcement")
    if not conventions.setting("issues.announce", True):
        return
    with tempfile.TemporaryDirectory(prefix="argus-issue-notice-") as directory:
        path = Path(directory) / "notice.md"
        path.write_text(
            f"Argus machine {machine} {messages[event]}\n", encoding="utf-8"
        )
        client.comment(number, path)


def main(
    argv: list[str] | None = None, *, _settings: dict[str, Any] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("queue", "retry", "escalate", "announce"))
    parser.add_argument("--config")
    parser.add_argument("--repo", "--repo-root", dest="config_repo_root")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--machine-id")
    parser.add_argument("--reservation-id")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--body-file")
    parser.add_argument("--event", choices=("released", "halted", "assignment-failed"))
    args = parser.parse_args(argv)
    try:
        if _settings is None:
            conventions.configure_from_args(args)
        else:
            conventions.configure(_settings)
        number = issue_number(args.issue)
        repo = Path(conventions.setting("repo-root")).resolve()
        machine = args.machine_id or conventions.setting("machine.id")
        client = IssueClient()
        if args.action == "announce":
            announce(client, number, machine, args.event)
        else:
            store = ReservationStore(repo, issues=True)
            state, _ = store.read()
            record = state["phases"].get(f"{number:06d}")
            if not record or record.get("machine_id") != machine:
                raise CoordinationError("issue claim belongs to another machine")
            if (
                args.reservation_id is not None
                and record.get("reservation_id") != args.reservation_id
            ):
                raise CoordinationError("issue claim is fenced")
            if (
                args.generation is not None
                and record.get("generation") != args.generation
            ):
                raise CoordinationError("issue claim generation is fenced")
            reservation = token(number, record)
            if args.action == "retry":
                retry(repo, store, client, number, record)
            elif args.action == "escalate":
                store.set_issue_receipt(
                    reservation, {"state_reason": "remedy_escalated", "cleared": False}
                )
                store.release_group(reservation)
            else:
                if not args.body_file:
                    raise IssuesUnavailable(
                        "writeback_payload", "queue requires --body-file"
                    )
                path = Path(args.body_file)
                path = (path if path.is_absolute() else repo / path).resolve()
                if not path.is_relative_to(repo):
                    raise IssuesUnavailable(
                        "writeback_payload", "report payload must be inside checkout"
                    )
                # The host's validating report helper is the caller of queue.
                receipt = {
                    "state_reason": "writeback_pending",
                    "body_file": path.relative_to(repo).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "validated": True,
                }
                store.set_issue_receipt(reservation, receipt)
    except (
        ConfigError,
        CoordinationError,
        IssuesUnavailable,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "state": "COORDINATION_UNAVAILABLE",
                    "kind": "issue",
                    "error": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
