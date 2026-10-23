# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read pull request stage history from GitHub."""

import json
import subprocess
from typing import Any, cast

from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary


def _json_object(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, Any], value)


def gh_pull_request_stage_history(
    number: int | str,
    owner: str | None,
    repo: str | None,
    host: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Read current PR stage and its complete ready/draft timeline.

    Missing or malformed data is unknown, never an empty history: a retry uses
    this answer to avoid a second ready transition.
    """
    query = """query(
      $owner: String!, $name: String!, $number: Int!, $endCursor: String
    ) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          state
          isDraft
          timelineItems(first: 100, after: $endCursor,
            itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT]) {
            nodes {
              __typename
              ... on ReadyForReviewEvent { createdAt }
              ... on ConvertToDraftEvent { createdAt }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }"""
    argv = [gh_binary(), "api", "graphql", "--paginate", "--slurp"]
    if host and host != "github.com":
        argv.extend(["--hostname", host])
    argv.extend(
        [
            "-f",
            f"owner={owner}",
            "-f",
            f"name={repo}",
            "-F",
            f"number={int(number)}",
            "-f",
            f"query={query}",
        ]
    )
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, "gh is not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh did not answer within {GH_TIMEOUT_S}s reading PR history"
    if done.returncode != 0:
        return None, gh_exit_detail(done.returncode, "the PR history read")
    try:
        raw_pages: object = json.loads(done.stdout)
    except ValueError:
        return None, "gh did not return JSON for the PR history"
    if not isinstance(raw_pages, list) or not raw_pages:
        return None, "gh returned no PR history pages"
    pages = cast(list[Any], raw_pages)
    state: str | None = None
    is_draft: bool | None = None
    events: list[dict[str, str]] = []
    for index, raw_page in enumerate(pages):
        page = _json_object(raw_page)
        if page is None or page.get("errors"):
            return None, "GitHub returned an incomplete PR history"
        data = _json_object(page.get("data"))
        repository = _json_object(data.get("repository")) if data else None
        pull_request = (
            _json_object(repository.get("pullRequest")) if repository else None
        )
        if pull_request is None:
            return None, "GitHub did not return the pull request history"
        page_state = pull_request.get("state")
        page_is_draft = pull_request.get("isDraft")
        if (
            not isinstance(page_state, str)
            or page_state not in {"OPEN", "CLOSED", "MERGED"}
            or not isinstance(page_is_draft, bool)
        ):
            return None, "GitHub returned an unclear pull request stage"
        if state is not None and (state != page_state or is_draft != page_is_draft):
            return None, "the pull request stage changed during the history read"
        state, is_draft = page_state, page_is_draft
        timeline = _json_object(pull_request.get("timelineItems"))
        page_info = _json_object(timeline.get("pageInfo")) if timeline else None
        raw_nodes = timeline.get("nodes") if timeline else None
        if not isinstance(raw_nodes, list) or page_info is None:
            return None, "GitHub returned an incomplete PR timeline"
        nodes = cast(list[Any], raw_nodes)
        has_next, cursor = page_info.get("hasNextPage"), page_info.get("endCursor")
        if not isinstance(has_next, bool) or (has_next and not isinstance(cursor, str)):
            return None, "GitHub returned an incomplete PR timeline page"
        if has_next != (index < len(pages) - 1):
            return None, "gh did not return every PR timeline page"
        for raw_node in nodes:
            node = _json_object(raw_node)
            if node is None:
                return None, "GitHub returned an invalid PR timeline event"
            kind, created_at = node.get("__typename"), node.get("createdAt")
            if (
                not isinstance(kind, str)
                or kind not in {"ReadyForReviewEvent", "ConvertToDraftEvent"}
                or not isinstance(created_at, str)
            ):
                return None, "GitHub returned an unclear PR timeline event"
            events.append({"type": kind, "created_at": created_at})
    return {"state": state, "is_draft": is_draft, "events": events}, ""


__all__ = ["gh_pull_request_stage_history"]
