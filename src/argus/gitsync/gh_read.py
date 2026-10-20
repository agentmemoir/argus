# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The gh reads: one pull request, its body, and the list for a branch."""

import json
import subprocess
from pathlib import Path
from typing import Any, cast

from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.gh_target import repo_target
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


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
    target = repo_target(owner, repo, host)
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
        # "authentication required"; the words keep the two apart.
        return None, gh_exit_detail(done.returncode, "the view")
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
    target = repo_target(owner, repo, host)
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
        return None, gh_exit_detail(done.returncode, "the body read")
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


def gh_default_branch(root: Path) -> tuple[str | None, str]:
    """Ask gh for the repository's default branch, from inside the checkout.

    This is the one source that knows a default branch this clone never
    fetched, so it is the last answer before a refusal. Returns the branch name
    and an empty detail, or ``None`` and why gh could not name it. ``None`` is
    never read as ``master``, or ``main``: a guess here is a wrong ``--base``
    and a bare gh exit on a real host.

    Its stderr is captured and discarded for the reason the pull-request view
    discards it: an unauthenticated call prints the host account name, and run
    logs are copied into issues.
    """
    argv = [gh_binary(), "repo", "view", "--json", "defaultBranchRef"]
    try:
        done = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return None, gh_exit_detail(done.returncode, "the default branch read")
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    ref = raw.get("defaultBranchRef") if isinstance(raw, dict) else None
    name = ref.get("name") if isinstance(ref, dict) else None
    if isinstance(name, str) and name:
        return name, ""
    return None, "gh reported no default branch"


def gh_pull_requests_for_branch(
    root: Path, branch: str, limit: int = 50
) -> tuple[list[dict[str, Any]] | None, str]:
    """Every pull request whose head is ``branch``, newest first.

    Returns the rows and an empty detail, or ``None`` and the reason gh could
    not answer. Every state is asked for, not only the open ones: a caller that
    looked at open pull requests alone would see no pull request at all for a
    branch whose work was already merged or closed, and would open a second one
    for the same commits.

    Three answers, and they are three different facts:

    * gh could not be asked: ``None`` and the reason.
    * gh answered rows and at least one of them parsed: the rows, and an empty
      detail. An empty row list here really does mean no pull request.
    * gh answered with rows and not one of them had a usable number, url, and
      state: ``None`` and a reason that names how many rows gh listed. That is
      a failed read, not "no pull request": a caller that read it as the latter
      would open a duplicate for commits that already have one.

    Each row carries ``number``, ``url``, ``isDraft``, and ``state``. A row
    missing a usable number, url, or state is dropped, because a caller reading
    a default in its place would report an invented state. ``isDraft`` is a
    real boolean or ``None``: gh gave that value or it did not, and the caller
    decides whether it can report the draft state without it.
    """
    argv = [
        gh_binary(),
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "all",
        "--limit",
        str(int(limit)),
        "--json",
        "number,url,isDraft,state",
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
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        return None, gh_exit_detail(done.returncode, "the list")
    try:
        parsed: object = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON"
    if not isinstance(parsed, list):
        return None, "gh returned JSON that is not a list"
    entries = cast("list[object]", parsed)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = cast("dict[str, Any]", entry)
        number = row.get("number")
        state = row.get("state")
        url = row.get("url")
        if not isinstance(number, int) or not isinstance(state, str):
            continue
        if not isinstance(url, str) or not url.strip():
            continue
        draft = row.get("isDraft")
        rows.append(
            {
                "number": number,
                "url": url.strip(),
                # Only a real boolean is a draft state. A missing flag, or the
                # string "false", is not: reporting either as true or false
                # would invent a value gh never gave.
                "isDraft": draft if isinstance(draft, bool) else None,
                "state": state,
            }
        )
    if not rows and entries:
        return None, (
            f"gh listed {len(entries)} pull request rows for {branch!r} and none "
            "of them had a usable number, url, and state"
        )
    return rows, ""
