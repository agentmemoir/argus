# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One read of a pull request's check rows, and gh's own exit code."""

import json
import subprocess
from pathlib import Path
from typing import Any, cast

from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.gh_target import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def _check_rows(text: str) -> list[dict[str, Any]] | None:
    """The check rows in gh's JSON output, or ``None`` when there were none.

    A row keeps its bucket, state, name, and link as strings. A row missing a
    field keeps the empty string in its place rather than being dropped: the
    count of rows gh reported is itself a fact, and an empty bucket is read by
    the caller as "not a pass", which is the safe direction.
    """
    try:
        parsed: object = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    # An empty JSON list is a fact gh reported, not a fault: a pull request
    # with no checks at all prints ``[]``. The caller refuses it, because zero
    # rows is not ``all checks pass``, but it must reach the caller as ``[]``.
    rows: list[dict[str, Any]] = []
    for entry in cast("list[object]", parsed):
        if not isinstance(entry, dict):
            continue
        row = cast("dict[str, Any]", entry)
        rows.append(
            {
                "bucket": str(row.get("bucket") or ""),
                "state": str(row.get("state") or ""),
                "name": str(row.get("name") or ""),
                "link": str(row.get("link") or ""),
            }
        )
    return rows


def gh_pull_request_checks(
    root: Path,
    target: int | str,
    owner: str | None = None,
    repo: str | None = None,
    host: str | None = None,
) -> tuple[list[dict[str, Any]] | None, int, str]:
    """Every check gh reports for one pull request, and gh's own exit code.

    ``owner`` and ``repo`` name the pull request's repository, and ``host`` names
    its host, in the same order as ``gh_pull_request_stage_history``. Both
    ``owner`` and ``repo`` are required: without them gh would answer about the
    repository of ``root``, which is wrong whenever the run directory sits
    outside a checkout or on a different repository than the pull request. A call
    missing either one is refused, and gh is not run. The host travels inside the
    one ``--repo [HOST/]OWNER/REPO`` argument: it is the only host-bearing flag
    ``gh pr checks`` accepts, so a second ``--hostname`` would be rejected by the
    command itself.

    ``target`` is a pull request number or URL. Returns the rows, the exit code
    gh returned, and a detail. The rows and the code are returned together so a
    caller can report what really happened instead of one number standing in
    for both: 0 means no failure and nothing pending, 1 means a fail bucket, and
    8 means pending. Exit 0 is never the whole answer here, because a skipping,
    neutral, or cancelled row is neither failed nor pending.

    When gh never ran, the rows are ``None``, the code is ``-1``, and the
    detail says why. When gh ran but printed nothing that parses as a list, the
    rows are ``None`` and the code is gh's. Only the code and the row list are
    reported, never gh's stderr: an unauthenticated call prints the host account
    name, and run logs are copied into issues.

    ``--required`` is deliberately absent. It narrows the list and hides a
    non-required failure, so a caller that used it would report a passing pull
    request with a failing check on it. ``--watch`` is absent too: this reads
    once and returns.
    """
    missing = [name for name, value in (("owner", owner), ("repo", repo)) if not value]
    if missing:
        return (
            None,
            -1,
            f"the checks read names no repository: {' and '.join(missing)} missing, "
            "so gh was not asked",
        )
    argv = [
        gh_binary(),
        "pr",
        "checks",
        str(int(target)) if isinstance(target, int) else str(target),
        "--json",
        "bucket,state,name,link",
        "--repo",
        repo_target(owner, repo, host),
    ]
    try:
        done = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
        )
    except FileNotFoundError:
        return None, -1, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, -1, f"gh did not answer within {GH_TIMEOUT_S}s"
    rows = _check_rows(done.stdout)
    if rows is None:
        # An empty branch has no checks, and gh says so on stderr with a
        # non-zero code. No row list is not an empty one for a caller's purpose
        # here, but both are reported so the caller can tell them apart: a code
        # of 0 with no JSON is a different fault from a code of 1.
        if done.returncode:
            return (
                None,
                done.returncode,
                f"{gh_exit_detail(done.returncode, 'the checks read')} with no checks",
            )
        return None, 0, f"{gh_exit_detail(0, 'the checks read')} without a check list"
    return rows, done.returncode, ""
