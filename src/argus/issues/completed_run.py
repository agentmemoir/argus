# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Whether an issue has an Argus pull request, for the driver's run gates."""

import subprocess
import unicodedata
from pathlib import Path

from argus.board.files import reservation_file
from argus.gitsync.issue_paths import issue_branch
from argus.gitsync.pr_signal_reply import SECRET_PATTERN
from argus.issues.client import IssueClient
from argus.issues.evidence import Evidence
from argus.issues.pulls import history
from argus.phases.runstate import state_in


def pull_request_verdict(number: int, task_name: str) -> str:
    """``pull_request`` when an Argus pull request exists for the issue.

    The issue branch and any suffixed branch count, in every state: open, closed,
    or merged. ``none`` means no pull request exists. A failed read raises.
    """
    client = IssueClient()
    pulls = client.pull_requests_on(issue_branch(number, task_name))
    return "pull_request" if pulls or history(client).get(number) else "none"


def owned(number: int) -> bool:
    """True when an open or merged Argus pull request owns the issue.

    The same rule selection uses. A failed read raises, so the caller resumes nothing.
    """
    return Evidence(IssueClient()).owns(number)


NO_CHANGE_FILE = "no-change.txt"
REASON_CAP = 200


def finished_unchecked(repo: Path, number: int) -> bool:
    """Whether the run completed with no checked no-change file, so no close waits."""
    run = reservation_file(repo, number, kind="issue").parent
    return state_in(run) == "completed" and not (run / NO_CHANGE_FILE).exists()


def _git_text(repo: Path, *args: str) -> str | None:
    """Stdout of one git command in the checkout, or None when it fails or cannot run."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def checked_no_change_reason(
    repo: Path, run_dir: Path, number: int, task_name: str
) -> str:
    """The agent's one-line reason, only when Argus confirms zero commits and a clean tree.

    Returns "" when the file is missing or unreadable, when the reason is empty or
    holds a secret-looking span, or when a check fails or cannot run. The scan reads
    the whole text with control characters gone, so neither the cap nor one of those
    characters can split a span. The reason stays the agent's claim.
    """
    branch = issue_branch(number, task_name)
    try:
        text = (run_dir / NO_CHANGE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    base = _git_text(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if base is None:
        return ""
    ahead = _git_text(repo, "rev-list", "--count", f"{base.strip()}..{branch}")
    dirty = _git_text(repo, "status", "--porcelain")
    if ahead is None or ahead.strip() != "0":
        return ""
    if dirty is None or dirty.strip():
        return ""
    joined = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    if SECRET_PATTERN.search(joined):
        return ""
    cleaned = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    line = " ".join(cleaned.split())
    return line[:REASON_CAP].rstrip() + ("..." if len(line) > REASON_CAP else "")
