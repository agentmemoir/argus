# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The GitHub CLI primitives: read one pull request, ask if it is open, mark it ready."""

import json
import subprocess
from typing import Any, cast

from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def _repo_target(owner: str | None, repo: str | None, host: str | None) -> str:
    """The ``[HOST/]OWNER/REPO`` argument gh wants for one pull request.

    The host is passed through rather than matched, so a GitHub Enterprise
    install works the same way github.com does. Only the literal ``github.com``
    is dropped, because it is gh's own default.
    """
    target = f"{owner}/{repo}"
    if host and host not in ("github.com",):
        return f"{host}/{target}"
    return target


def _gh_pull_request_view(
    number: int | str, owner: str | None, repo: str | None, host: str | None
) -> tuple[dict[str, Any] | None, str]:
    """Ask gh for one pull request's state fields.

    Returns the parsed JSON object and an empty detail, or ``None`` and the
    reason GitHub could not be asked. ``gh`` is invoked rather than a hand-rolled
    HTTP call because ``gh`` already holds the credential in the host keychain
    and keeps it out of every log, file, and command line, which is the whole
    reason ``github_issues.py`` exists. A second token path is a second thing to
    audit.

    Its stderr is captured and discarded rather than reported. An
    unauthenticated call prints the host account name into the message, and run
    logs are copied into issues; the exit status is what this needs and the
    status alone is enough to separate an answer from "could not ask".
    """
    target = _repo_target(owner, repo, host)
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(int(number)),
        "--repo",
        target,
        "--json",
        "state,mergedAt,mergeCommit",
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        # Exit status only, never the message. 1 is a general failure and 4 is
        # "authentication required"; both mean the same thing here.
        return None, f"gh exited {done.returncode}"
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    if not isinstance(raw, dict):
        return None, "gh returned JSON that is not an object"
    return cast("dict[str, Any]", raw), ""


def gh_pull_request_state(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[str, str]:
    """Ask GitHub whether a pull request is merged.

    Returns ``(state, detail)`` where state is ``merged``, ``not-merged``, or
    ``unknown``. Three states, not two: a pipeline whose answer is binary
    cannot tell "GitHub says not merged" from "GitHub could not be reached",
    and only one of those is an answer. ``unknown`` must never be read as
    merged, so every caller falls back to the commit-id test, which fails
    closed.
    """
    data, why = _gh_pull_request_view(number, owner, repo, host)
    if data is None:
        return "unknown", why
    state = str(data.get("state") or "")
    if state == "MERGED":
        # mergeCommit, not mergedAt, is the durable record: it names the commit
        # GitHub created, and it is populated for a rebase merge where mergedAt
        # is not. Both are read defensively because a field's shape in gh's JSON
        # is gh's, not GitHub's, and this pipeline cannot exercise it against a
        # live pull request without a token it must not reach for.
        commit = data.get("mergeCommit")
        oid = (
            commit
            if isinstance(commit, str)
            else (
                cast("dict[str, Any]", commit).get("oid")
                if isinstance(commit, dict)
                else None
            )
        )
        return "merged", oid or str(data.get("mergedAt") or "")
    if state in {"OPEN", "CLOSED"}:
        # CLOSED is a closed-without-merging pull request, which is a real
        # answer and not a pending one. Either way it is not merged.
        return "not-merged", state
    return "unknown", f"unrecognised state {state!r}"


def gh_pull_request_open(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[bool | None, str]:
    """Ask GitHub whether a pull request is still open.

    Returns ``(True, "OPEN")`` while it is open, ``(False, state)`` once it is
    closed or merged, and ``(None, why)`` when GitHub could not be asked.
    ``None`` is not ``False``: a caller that read "could not ask" as "closed"
    would throw away a live pull request, and one that read it as "open" would
    keep a dead one. The three answers keep the two apart.
    """
    data, why = _gh_pull_request_view(number, owner, repo, host)
    if data is None:
        return None, why
    state = str(data.get("state") or "")
    if state == "OPEN":
        return True, state
    if state in {"CLOSED", "MERGED"}:
        return False, state
    return None, f"unrecognised state {state!r}"


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


def gh_pull_request_body(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[str | None, str]:
    """Read a pull request body, or None when GitHub cannot answer.

    Returns ``(body, detail)`` where body is the text gh reports. None is not
    empty: a caller that reads "could not ask" as empty would wipe the body.
    """
    target = _repo_target(owner, repo, host)
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(int(number)),
        "--repo",
        target,
        "--json",
        "body",
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return None, f"gh exited {done.returncode}"
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    if not isinstance(raw, dict):
        return None, "gh returned JSON without a body"
    body_value = cast("dict[str, Any]", raw).get("body")
    if not isinstance(body_value, str):
        return None, "gh returned JSON without a body"
    return body_value, ""


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


__all__ = [
    "gh_comment_pull_request",
    "gh_edit_pull_request_body",
    "gh_mark_pull_request_ready",
    "gh_pull_request_body",
    "gh_pull_request_open",
    "gh_pull_request_state",
]
