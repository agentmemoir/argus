# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-tests for pending group publication and pull-request readiness."""

import json
import shutil
from pathlib import Path

from argus.gitsync.group_review import _group_pending, mark_group_pull_request_ready
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _StubGH,
    gh_group_ready_responses,
)
from argus.gitsync.selftest_group_fixtures import (
    persist_ready_attempt as _persist_ready_attempt,
)
from argus.gitsync.selftest_group_fixtures import (
    write_receipt as _write_receipt,
)


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
            mark_group_pull_request_ready(
                pub,
                100030,
                members,
                pending,
                url,
                persist_attempt=_persist_ready_attempt,
            )
            is None,
            f"pending={pending}",
        )
    )
    results.append(
        (
            "the ready transition does nothing without a pull request to act on",
            mark_group_pull_request_ready(
                pub,
                100030,
                members,
                [],
                None,
                persist_attempt=_persist_ready_attempt,
            )
            is None
            and mark_group_pull_request_ready(
                pub,
                100030,
                members,
                [],
                "not a url",
                persist_attempt=_persist_ready_attempt,
            )
            is None,
            "no pull request, no action",
        )
    )
    # The refusal check runs before any successful ready: once the ready-once
    # record stands, the same pull request is skipped without asking gh, so a
    # refusal after that record exists would never be seen.
    with _StubGH(gh_group_ready_responses({"returncode": 1})):
        ready = mark_group_pull_request_ready(
            pub,
            100030,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
    results.append(
        (
            "a refused ready transition is reported as not ok",
            isinstance(ready, dict)
            and ready.get("ok") is False
            and not ready.get("skipped"),
            f"ready={ready}",
        )
    )
    marker = pub / BOARD_REL / "runs" / "phase-100030" / "group-pr-ready.json"
    results.append(
        (
            "a refused ready writes no ready-once record",
            not marker.exists(),
            f"marker exists={marker.exists()}",
        )
    )
    # gh returning zero rows is not "all checks pass": an empty report names no
    # passing check, so the ready is refused and the pull request stays draft.
    with _StubGH(gh_group_ready_responses({"returncode": 0, "stdout": "[]"})):
        ready = mark_group_pull_request_ready(
            pub,
            100030,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
    results.append(
        (
            "an empty check report is a refusal, not a pass",
            isinstance(ready, dict)
            and ready.get("ok") is False
            and not marker.exists(),
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
        ready = mark_group_pull_request_ready(
            pub,
            100030,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
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
    results.append(
        (
            "a successful ready writes the ready-once record beside the receipt",
            marker.is_file()
            and json.loads(marker.read_text()).get("pull_request") == url,
            f"marker={marker.read_text().strip() if marker.is_file() else 'absent'}",
        )
    )
    prior_ready = {
        "returncode": 0,
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
        ),
    }
    skip = _StubGH(prior_ready)
    with skip:
        ready = mark_group_pull_request_ready(
            pub,
            100030,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
    results.append(
        (
            "a record naming the same pull request skips the ready",
            isinstance(ready, dict)
            and ready.get("ok") is True
            and ready.get("skipped") is True
            and not any(argv[1:3] == ["pr", "ready"] for argv in skip.calls if argv),
            f"ready={ready} calls={skip.calls}",
        )
    )
    with _StubGH(prior_ready) as sibling_skip:
        sibling_ready = mark_group_pull_request_ready(
            pub,
            100020,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
    results.append(
        (
            "a sibling publish sees the shared ready-once record",
            isinstance(sibling_ready, dict)
            and sibling_ready.get("ok") is True
            and sibling_ready.get("skipped") is True
            and not any(
                argv[1:3] == ["pr", "ready"] for argv in sibling_skip.calls if argv
            ),
            f"ready={sibling_ready} calls={sibling_skip.calls}",
        )
    )
    other = "https://github.com/example-org/example-repo/pull/9"
    marker.write_text(
        json.dumps(
            {"version": 1, "pull_request": other, "ready_at": "2026-01-01T00:00:00Z"}
        )
        + "\n"
    )
    with _StubGH(gh_group_ready_responses(green)):
        ready = mark_group_pull_request_ready(
            pub,
            100030,
            members,
            [],
            url,
            persist_attempt=_persist_ready_attempt,
        )
    results.append(
        (
            "a record naming another pull request re-evaluates the ready",
            isinstance(ready, dict)
            and ready.get("ok") is True
            and not ready.get("skipped")
            and json.loads(marker.read_text()).get("pull_request") == url,
            f"ready={ready}",
        )
    )
