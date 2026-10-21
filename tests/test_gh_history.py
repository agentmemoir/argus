# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""GitHub PR history must include every ready and draft timeline page."""

import json
import subprocess

import pytest

import argus.gitsync.gh_history as gh_history


def test_stage_history_reader_requires_every_timeline_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def page(nodes: list[dict[str, str]], has_next: bool, cursor: str | None) -> dict:
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "state": "OPEN",
                        "isDraft": True,
                        "timelineItems": {
                            "nodes": nodes,
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": cursor,
                            },
                        },
                    }
                }
            }
        }

    ready_event = {
        "__typename": "ReadyForReviewEvent",
        "createdAt": "2026-01-01T00:00:00Z",
    }
    draft_event = {
        "__typename": "ConvertToDraftEvent",
        "createdAt": "2026-01-02T00:00:00Z",
    }
    pages = [page([ready_event], True, "cursor-1"), page([draft_event], False, None)]
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages), "")

    monkeypatch.setattr(gh_history, "gh_binary", lambda: "gh")
    monkeypatch.setattr(gh_history.subprocess, "run", run)
    history, detail = gh_history.gh_pull_request_stage_history(
        7, "example-org", "example-repo"
    )

    assert detail == ""
    assert history == {
        "state": "OPEN",
        "is_draft": True,
        "events": [
            {"type": "ReadyForReviewEvent", "created_at": ready_event["createdAt"]},
            {"type": "ConvertToDraftEvent", "created_at": draft_event["createdAt"]},
        ],
    }
    assert "--paginate" in calls[0]
    assert "--slurp" in calls[0]
    assert "READY_FOR_REVIEW_EVENT" in calls[0][-1]
    assert "CONVERT_TO_DRAFT_EVENT" in calls[0][-1]

    incomplete = [page([ready_event], True, "cursor-1")]

    def incomplete_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, json.dumps(incomplete), "")

    monkeypatch.setattr(gh_history.subprocess, "run", incomplete_run)
    history, detail = gh_history.gh_pull_request_stage_history(
        7, "example-org", "example-repo"
    )
    assert history is None
    assert "every PR timeline page" in detail
