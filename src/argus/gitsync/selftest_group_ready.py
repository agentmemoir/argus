# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-tests for pending group publication and pull-request readiness."""

import json
import shutil
from pathlib import Path
from typing import Any

from argus.gitsync.group_review import _group_pending, mark_group_pull_request_ready
from argus.gitsync.modes import mode_ready
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _StubGH,
    gh_group_ready_responses,
)
from argus.gitsync.selftest_group_fixtures import write_receipt as _write_receipt


def _ready_calls(calls: list[Any]) -> int:
    """How many ``gh pr ready`` calls a stub recorded."""
    return sum(1 for argv in calls if list(argv[1:3]) == ["pr", "ready"])


def pending_checks(pub: Path, results: list[tuple[str, object, str]]) -> None:
    members = ["100020", "100030"]
    url = "https://github.com/example-org/example-repo/pull/7"
    _write_receipt(
        pub, 100020, state="awaiting_review", branch="b", group=list(members)
    )
    pending = _group_pending(pub, members, 100030)
    results.append(
        (
            "the members still to publish exclude this phase and the ones with "
            "a receipt",
            pending == [],
            f"pending={pending}",
        )
    )
    shutil.rmtree(pub / BOARD_REL / "runs" / "phase-100020")
    pending = _group_pending(pub, members, 100030)
    results.append(
        (
            "a member with no receipt is still to publish",
            pending == ["100020"],
            f"pending={pending}",
        )
    )
    results.append(
        (
            "the ready transition does nothing while a member is still to run",
            mark_group_pull_request_ready(pub, 100030, members, pending, url) is None,
            f"pending={pending}",
        )
    )
    results.append(
        (
            "the ready transition does nothing without a pull request to act on",
            mark_group_pull_request_ready(pub, 100030, members, [], None) is None
            and mark_group_pull_request_ready(pub, 100030, members, [], "not a url")
            is None,
            "no pull request, no action",
        )
    )
    with _StubGH(gh_group_ready_responses({"returncode": 1})):
        ready = mark_group_pull_request_ready(pub, 100030, members, [], url)
    results.append(
        (
            "a refused ready transition is reported as not ok",
            isinstance(ready, dict)
            and ready.get("ok") is False
            and not ready.get("skipped"),
            f"ready={ready}",
        )
    )
    # gh returning zero rows is not "all checks pass": an empty report names no
    # passing check, so the ready is refused and the pull request stays draft.
    with _StubGH(gh_group_ready_responses({"returncode": 0, "stdout": "[]"})):
        ready = mark_group_pull_request_ready(pub, 100030, members, [], url)
    results.append(
        (
            "an empty check report is a refusal, not a pass",
            isinstance(ready, dict) and ready.get("ok") is False,
            f"ready={ready}",
        )
    )
    green = {
        "returncode": 0,
        "stdout": json.dumps(
            [{"bucket": "pass", "state": "success", "name": "ci", "link": ""}]
        ),
    }
    with _StubGH(gh_group_ready_responses(green)):
        ready = mark_group_pull_request_ready(pub, 100030, members, [], url)
    results.append(
        (
            "the last member marks the shared pull request ready",
            isinstance(ready, dict)
            and ready.get("ok") is True
            and ready.get("pull_request") == url
            and ready.get("phase") == "100030",
            f"ready={ready}",
        )
    )
    draft = {
        "stdout": json.dumps(
            [
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "state": "OPEN",
                                "isDraft": True,
                                "timelineItems": {
                                    "nodes": [],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                },
                            }
                        }
                    }
                }
            ]
        )
    }
    prior_ready = {
        "stdout": json.dumps(
            [
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "state": "OPEN",
                                "isDraft": False,
                                "timelineItems": {
                                    "nodes": [
                                        {
                                            "__typename": "ReadyForReviewEvent",
                                            "createdAt": "2026-01-01T00:00:00Z",
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                },
                            }
                        }
                    }
                }
            ]
        )
    }
    settle = _StubGH(prior_ready)
    with settle:
        ready = mark_group_pull_request_ready(pub, 100030, members, [], url)
    results.append(
        (
            "a ready GitHub already recorded skips the transition",
            isinstance(ready, dict)
            and ready.get("ok") is True
            and ready.get("skipped") is True
            and _ready_calls(settle.calls) == 0,
            f"ready={ready} calls={settle.calls}",
        )
    )
    # The per-tick sweep is the retry: it walks every receipt, the settled ones
    # included, and takes the transition at most once per pull request because
    # GitHub's own ready/draft timeline is the guard. The stub records the ready
    # it was asked for, so the second tick sees the history GitHub would hold.
    state = {"ready": False}

    def sweep_gh(argv: Any) -> dict[str, Any]:
        if list(argv[1:3]) == ["pr", "ready"]:
            state["ready"] = True
            return {"returncode": 0}
        if list(argv[1:3]) == ["api", "graphql"]:
            return prior_ready if state["ready"] else draft
        if list(argv[1:3]) == ["pr", "list"]:
            return {"returncode": 0, "stdout": json.dumps([{"number": 7}])}
        if list(argv[1:3]) == ["pr", "checks"]:
            return green
        return {"returncode": 1}

    for member in members:
        _write_receipt(
            pub,
            int(member),
            state="published",
            branch="b",
            group=list(members),
            pull_request=url,
        )
    tick = _StubGH(sweep_gh)
    with tick:
        swept = mode_ready(pub)
    results.append(
        (
            "the ready sweep readies a settled group's pull request",
            swept.get("state") == "READY"
            and swept.get("ok") is True
            and _ready_calls(tick.calls) == 1,
            f"swept={swept} calls={tick.calls}",
        )
    )
    later = _StubGH(sweep_gh)
    with later:
        again = mode_ready(pub)
    results.append(
        (
            "a later sweep takes no second ready and says why",
            again.get("state") == "READY"
            and _ready_calls(later.calls) == 0
            and all(
                one.get("skipped") is True
                for one in again["results"]
                if one.get("state") == "READY"
            ),
            f"again={again} calls={later.calls}",
        )
    )
    run_dir = pub / BOARD_REL / "runs" / "phase-100030"
    results.append(
        (
            "the sweep writes no ready record of its own",
            sorted(path.name for path in run_dir.iterdir()) == ["publication.json"],
            f"run dir holds={sorted(path.name for path in run_dir.iterdir())}",
        )
    )
