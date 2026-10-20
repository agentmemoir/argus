# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The gh writes: mark ready, edit a body, comment, and create a draft."""

import subprocess
from pathlib import Path

from argus.gitsync.gh_target import _repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def gh_mark_pull_request_ready(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[bool, str]:
    """Take a pull request out of draft.

    ``gh pr ready`` is idempotent: a pull request that is already ready is left
    alone and gh exits 0, so running this again for the same pull request is
    safe. A closed pull request exits non-zero, which is reported rather than
    hidden. Returns ``(ok, detail)``.
    """
    target = _repo_target(owner, repo, host)
    argv = [gh_binary(), "pr", "ready", str(int(number)), "--repo", target]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return False, "gh is not installed"
    except subprocess.TimeoutExpired:
        return False, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return False, f"gh exited {done.returncode}"
    return True, f"pull request {int(number)} is ready for review"


def gh_edit_pull_request_body(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None,
    body: str,
) -> tuple[bool, str]:
    """Replace a pull request body. Returns ``(ok, detail)``.

    A failure is reported, never raised. The caller keeps its own state, so a
    GitHub blip must not undo a publication that already landed.
    """
    target = _repo_target(owner, repo, host)
    argv = [
        gh_binary(),
        "pr",
        "edit",
        str(int(number)),
        "--repo",
        target,
        "--body",
        body,
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return False, "gh is not installed"
    except subprocess.TimeoutExpired:
        return False, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return False, f"gh exited {done.returncode}"
    return True, f"pull request {int(number)} body updated"


def gh_comment_pull_request(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None,
    body: str,
) -> tuple[bool, str]:
    """Post a comment on a pull request. Returns ``(ok, detail)``."""
    target = _repo_target(owner, repo, host)
    argv = [
        gh_binary(),
        "pr",
        "comment",
        str(int(number)),
        "--repo",
        target,
        "--body",
        body,
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return False, "gh is not installed"
    except subprocess.TimeoutExpired:
        return False, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return False, f"gh exited {done.returncode}"
    return True, f"pull request {int(number)} comment posted"


def gh_create_draft_pull_request(
    root: Path,
    branch: str,
    base: str,
    title: str | None = None,
    body: str | None = None,
) -> tuple[str | None, str]:
    """Create a draft pull request from ``branch`` into ``base``.

    The title defaults to the branch name and the body to the empty string, so
    a caller that passes neither gets exactly what this function always did.
    Returns gh's own output, which is the new pull request's URL, or ``None``
    and why gh could not create it.
    """
    try:
        done = subprocess.run(
            [
                gh_binary(),
                "pr",
                "create",
                "--draft",
                "--head",
                branch,
                "--base",
                base,
                "--title",
                branch if title is None else title,
                "--body",
                "" if body is None else body,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode:
        return None, f"gh exited {done.returncode}"
    return done.stdout.strip(), ""
