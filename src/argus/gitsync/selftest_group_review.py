# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group pull-request checks: reuse while open, and the ready transition."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.gh import gh_mark_pull_request_ready, gh_pull_request_open
from argus.gitsync.git import GH_TIMEOUT_S
from argus.gitsync.group_review import (
    _group_pull_request,
)
from argus.gitsync.selftest_fixture import (
    _gh_says,
    _StubGH,
)
from argus.gitsync.selftest_group_fixtures import (
    receipt_fixture as _receipt_fixture,
)
from argus.gitsync.selftest_group_fixtures import (
    write_receipt as _write_receipt,
)
from argus.gitsync.selftest_group_ready import pending_checks


def _open_question_checks(results: list[tuple[str, object, str]]) -> None:
    wrong: list[tuple[str, object, str]] = []
    for state, want in (("OPEN", True), ("CLOSED", False), ("MERGED", False)):
        with _StubGH(_gh_says(state)):
            got, detail = gh_pull_request_open(42, "example-org", "example-repo")
        if got is not want:
            wrong.append((state, got, detail))
    results.append(
        (
            "gh reporting OPEN, CLOSED or MERGED decides whether the pull "
            "request is open",
            not wrong,
            f"mismatches={wrong}",
        )
    )
    # Unknown is not False. A caller that read "could not ask" as "closed" would
    # throw away a live pull request, so the two answers are pinned apart.
    unanswerable: list[tuple[Any, str]] = [
        ({"returncode": 1}, "gh exited 1"),
        ({"returncode": 4}, "gh exited 4"),
        ({"stdout": "not json"}, "gh did not return JSON"),
        ({"stdout": json.dumps({"state": "WHATEVER"})}, "unrecognised state"),
        (FileNotFoundError(), "gh is not installed"),
        (subprocess.TimeoutExpired(cmd="gh", timeout=GH_TIMEOUT_S), "timeout"),
    ]
    wrong = []
    for answer, _label in unanswerable:
        with _StubGH(answer):
            got, detail = gh_pull_request_open(42, "example-org", "example-repo")
        if got is not None:
            wrong.append((answer, got, detail))
    results.append(
        (
            "a pull request GitHub cannot answer about reads unknown, not closed",
            not wrong,
            f"mismatches={wrong}",
        )
    )


def _ready_transition_checks(results: list[tuple[str, object, str]]) -> None:
    with _StubGH({"returncode": 0, "stdout": ""}):
        ok, detail = gh_mark_pull_request_ready(7, "example-org", "example-repo")
    results.append(
        (
            "gh pr ready exiting 0 is a ready pull request",
            ok and "7" in detail,
            f"ok={ok} detail={detail}",
        )
    )
    with _StubGH({"returncode": 1}):
        ok, detail = gh_mark_pull_request_ready(7, "example-org", "example-repo")
    results.append(
        (
            "a ready transition gh refuses is reported, not swallowed",
            (not ok) and "1" in detail,
            f"ok={ok} detail={detail}",
        )
    )


def _reuse_checks(pub: Path, results: list[tuple[str, object, str]]) -> None:
    branch = "feature/group-cache-warm"
    members = ["100020", "100030"]
    url = "https://github.com/example-org/example-repo/pull/7"
    _write_receipt(
        pub,
        100020,
        state="awaiting_review",
        branch=branch,
        group=list(members),
        pull_request=url,
    )

    def reuse() -> Any:
        return _group_pull_request(pub, members, branch, 100030)

    for state, want, label in (
        ("OPEN", url, "a sibling's pull request still open is reused"),
        ("MERGED", None, "a sibling's merged pull request is not reused"),
        ("CLOSED", None, "a sibling's closed pull request is not reused"),
    ):
        with _StubGH(_gh_says(state)):
            got = reuse()
        results.append((label, got == want, f"state={state} got={got}"))
    # The incident's second case: GitHub cannot be asked, so the record is kept
    # rather than dropped. The merge check degrades with GitHub anyway, and a
    # member that names no pull request at all is worse off.
    with _StubGH({"returncode": 1}):
        got = reuse()
    results.append(
        (
            "a sibling's pull request is reused when GitHub cannot be asked",
            got == url,
            f"got={got}",
        )
    )
    # A settled sibling: its own pull request merged, so it cannot carry the
    # member being published now, whatever GitHub would say about it.
    _write_receipt(
        pub,
        100020,
        state="published",
        branch=branch,
        group=list(members),
        pull_request=url,
    )
    with _StubGH(_gh_says("OPEN")):
        got = reuse()
    results.append(
        (
            "a sibling receipt already reading published is never reused",
            got is None,
            f"got={got}",
        )
    )
    # A receipt on another branch is another group's pull request.
    _write_receipt(
        pub,
        100020,
        state="awaiting_review",
        branch="feature/other-group",
        group=list(members),
        pull_request=url,
    )
    with _StubGH(_gh_says("OPEN")):
        got = reuse()
    results.append(
        (
            "a receipt on another branch is not this group's pull request",
            got is None,
            f"got={got}",
        )
    )


def _group_review_checks(base: Path) -> list[tuple[str, object, str]]:
    results: list[tuple[str, object, str]] = []
    _open_question_checks(results)
    _ready_transition_checks(results)
    pub = _receipt_fixture(base, "group-review")
    _reuse_checks(pub, results)
    pending_checks(pub, results)
    return results


__all__ = ["_group_review_checks", "_receipt_fixture", "_write_receipt"]
