# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group pull-request checks: reuse while open, and the ready transition."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.gh import gh_mark_pull_request_ready, gh_pull_request_open
from argus.gitsync.git import GH_TIMEOUT_S
from argus.gitsync.group_review import (
    _group_pending,
    _group_pull_request,
    mark_group_pull_request_ready,
)
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _configure_self_test,
    _gh_says,
    _StubGH,
)


def _receipt_fixture(base: Path, name: str) -> Path:
    """A configured checkout with no git history: only receipts are read.

    The group's pull-request policy reads the run directories and nothing else,
    so the fixture writes receipts and stops there. No commit, no remote, and no
    agent run is needed, which is what keeps these checks fast.
    """
    pub = base / name / "pub"
    pub.mkdir(parents=True)
    _configure_self_test(pub, pub / BOARD_REL)
    return pub


def _write_receipt(pub: Path, number: int, **fields: Any) -> None:
    run_dir = pub / BOARD_REL / "runs" / f"phase-{number:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "publication.json").write_text(
        json.dumps({"phase": f"{number:06d}", **fields}) + "\n"
    )


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


def _pending_checks(pub: Path, results: list[tuple[str, object, str]]) -> None:
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
            mark_group_pull_request_ready(100030, pending, url) is None,
            f"pending={pending}",
        )
    )
    results.append(
        (
            "the ready transition does nothing without a pull request to act on",
            mark_group_pull_request_ready(100030, [], None) is None
            and mark_group_pull_request_ready(100030, [], "not a url") is None,
            "no pull request, no action",
        )
    )
    with _StubGH({"returncode": 0, "stdout": ""}):
        ready = mark_group_pull_request_ready(100030, [], url)
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
    with _StubGH({"returncode": 1}):
        ready = mark_group_pull_request_ready(100030, [], url)
    results.append(
        (
            "a refused ready transition is reported as not ok",
            isinstance(ready, dict) and ready.get("ok") is False,
            f"ready={ready}",
        )
    )


def _group_review_checks(base: Path) -> list[tuple[str, object, str]]:
    results: list[tuple[str, object, str]] = []
    _open_question_checks(results)
    _ready_transition_checks(results)
    pub = _receipt_fixture(base, "group-review")
    _reuse_checks(pub, results)
    _pending_checks(pub, results)
    return results


__all__ = ["_group_review_checks"]
