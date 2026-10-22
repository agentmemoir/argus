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
import re
import subprocess
import sys
import time
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.gitsync import repair_round
from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary

# A GitHub Actions job link: the repository and the job id are all gh needs.
_JOB_LINK = re.compile(r"https://([^/]+)/([^/]+/[^/]+)/actions/runs/\d+/job/(\d+)")
TAIL_LINES = 40


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


def save_failed_log(
    run_dir: Path, key: str, failing: list[dict[str, str]]
) -> tuple[list[str], str | None]:
    """Save the first failing job's failed-step log; return its tail and path.

    Only a GitHub Actions job link is read. Any other link, or a gh call that
    fails or times out, gives no log: the task file then says "not saved".
    """
    for check in failing:
        match = _JOB_LINK.match(check.get("link", ""))
        if not match:
            continue
        host, repo, job = match.groups()
        target = repo if host == "github.com" else f"{host}/{repo}"
        argv = [gh_binary(), "run", "view", "--job", job, "--log-failed"]
        try:
            done = subprocess.run(
                [*argv, "--repo", target],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GH_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            return [], None
        if done.returncode:
            return [], None
        path = run_dir / f"repair-{key}.log"
        path.write_text(done.stdout, encoding="utf-8")
        return done.stdout.splitlines()[-TAIL_LINES:], str(path)
    return [], None


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
        tail, log_path = save_failed_log(args.run_dir, key, failing)
        task.write_text(
            repair_round.task_text(args.pull_request, failing, tail, log_path),
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
    # The driver also passes its configuration flags; configure_from_cli reads them.
    args, _config = build_parser().parse_known_args(argv)
    try:
        rc, out = run(args, time.time() if now is None else now)
    except ValueError as exc:
        print(f"repair_cli: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    return rc


if __name__ == "__main__":
    # gh_binary reads commands.gh, so the settings load before any gh call.
    conventions.configure_from_cli(sys.argv[1:])
    sys.exit(main())
