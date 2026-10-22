# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Command entry for one tick's CI repair step on one pull request.

The driver runs it the way it runs ``board/remedy.py``: ``"$PYTHON" "$ARGUS/
gitsync/repair_cli.py" ...``. It reads the checks once, decides with
``repair_round.decide``, and prints one JSON object: ``action`` (``none``,
``wait``, ``repair``, ``escalate``), plus ``task`` on repair and ``message``
on escalate. A repair round is counted when it is decided, before the launch,
so a launch that fails still uses one round and the count stays bounded.

A checks read that fails exits 3 and changes nothing: an unread pull request
is not a green one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus.gitsync import repair_round
from argus.gitsync.gh_checks import gh_pull_request_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pull-request", required=True, help="number or URL")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=int)
    parser.add_argument("--wait", required=True, type=int)
    return parser


def _key(pull_request: str) -> str:
    """A file-name-safe key for one pull request: its trailing number."""
    tail = pull_request.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise ValueError(f"pull request {pull_request!r} has no number")
    return tail


def run(args: argparse.Namespace, now: float) -> tuple[int, dict[str, object]]:
    key = _key(args.pull_request)
    rows, code, _detail = gh_pull_request_checks(args.run_dir, args.pull_request)
    if rows is None:
        return 3, {"action": "unread", "exit_code": code}
    record_path = args.run_dir / f"repair-{key}.json"
    record = repair_round.load_record(record_path)
    action, due = repair_round.decide(
        rows, record, now=now, attempts=args.attempts, wait=args.wait
    )
    failing = repair_round.failing_checks(rows)
    out: dict[str, object] = {"action": action}
    if action == "wait":
        out["due"] = due
    elif action == "repair":
        task = args.run_dir / f"repair-{key}.md"
        task.write_text(
            repair_round.task_text(args.pull_request, failing, [], None),
            encoding="utf-8",
        )
        record["attempts"] = [*record.get("attempts", []), now]
        repair_round.save_record(record_path, record)
        out["task"] = str(task)
    elif action == "escalate":
        record["escalated"] = True
        repair_round.save_record(record_path, record)
        out["message"] = repair_round.escalation_text(
            args.pull_request, args.attempts, failing
        )
    return 0, out


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rc, out = run(args, time.time() if now is None else now)
    except ValueError as exc:
        print(f"repair_cli: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
