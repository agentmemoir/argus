# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every Argus pull request on an issue branch, in any state, keyed by issue number."""

from typing import Any, cast

from argus.issues.client import BRANCH_RE, IssueClient
from argus.issues.parse import objects
from argus.issues.transport import IssuesUnavailable

STATES = frozenset({"open", "closed", "merged"})


def history(client: IssueClient) -> dict[int, list[dict[str, Any]]]:
    """Every Argus pull request, open, closed, or merged, read in full.

    A suffixed branch such as issue/<n>-<slug>-2 belongs to the same issue. A
    malformed row or an unknown state raises, so a partial read selects nothing.
    """
    path = f"repos/{client.repo}/pulls?state=all&per_page=100"
    pages = client.call(["api", "--paginate", "--slurp", path])
    if not isinstance(pages, list):
        raise IssuesUnavailable("invalid_response", "pr history")
    past: dict[int, list[dict[str, Any]]] = {}
    for page in cast("list[Any]", pages):
        for row in objects(page, "pr history"):
            head = row.get("head")
            head = cast("dict[str, Any]", head) if isinstance(head, dict) else {}
            repo = head.get("repo")
            owner = cast("dict[str, Any]", repo) if isinstance(repo, dict) else {}
            name = owner.get("full_name")
            ref = head.get("ref")
            match = BRANCH_RE.fullmatch(ref) if isinstance(ref, str) else None
            if (
                match is None
                or not isinstance(name, str)
                or name.casefold() != client.repo.casefold()
            ):
                continue
            state = "merged" if row.get("merged_at") else str(row.get("state", ""))
            state = state.lower()
            if (
                state not in STATES
                or not isinstance(row.get("number"), int)
                or not isinstance(row.get("html_url"), str)
            ):
                raise IssuesUnavailable("invalid_response", "pr history")
            past.setdefault(int(match.group(1)), []).append(
                {
                    "number": row["number"],
                    "url": row["html_url"],
                    "branch": ref,
                    "state": state,
                }
            )
    return past
