# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group gate checks: fix pull requests merge only when safe, ready waits for green."""

from pathlib import Path
from typing import Any

from argus.conventions import runs_dir
from argus.gitsync.group_gate import (
    checks_verdict,
    merge_fix_pull_requests,
    mode_gate,
    release_group_pull_request,
)
from argus.gitsync.review import open_predecessor
from argus.gitsync.selftest_gate_fixture import (
    BRANCH,
    HEAD,
    MEMBERS,
    REF,
    URL,
    _checks,
    _listing,
    _pr,
    _RoutedGH,
    _rows,
)
from argus.gitsync.selftest_group_review import _receipt_fixture, _write_receipt

Result = tuple[str, object, str]


def _verdict_checks() -> list[Result]:
    rows = [{"name": "b", "bucket": "fail"}]
    cases: list[tuple[str, Any, tuple[str, list[str]]]] = [
        ("no rows", [], ("unknown", [])),
        ("unanswered", None, ("unknown", [])),
        ("all pass or skip", _rows("pass", "skipping"), ("passing", [])),
        ("one failing", _rows("pass") + rows, ("failing", ["b"])),
        ("cancelled", [{"name": "c", "bucket": "cancel"}], ("failing", ["c"])),
        ("running", _rows("pass", "pending"), ("pending", ["job2"])),
        (
            "a bucket gh adds later",
            [{"name": "n", "bucket": "new"}],
            ("pending", ["n"]),
        ),
        ("failing beats running", _rows("pending") + rows, ("failing", ["b"])),
    ]
    wrong = [
        (n, checks_verdict(r)) for n, r, want in cases if checks_verdict(r) != want
    ]
    return [("the checks verdict reads each bucket", not wrong, f"wrong={wrong}")]


def _fix_merge_checks() -> list[Result]:
    results: list[Result] = []
    ref = {**REF, "number": 7}
    green = {"pr list": _listing(_pr(11)), "pr checks": _checks("pass"), "pr merge": {}}
    with _RoutedGH(green) as gh:
        got = merge_fix_pull_requests(BRANCH, ref)
    merge = gh.ran("pr merge")
    listing = gh.ran("pr list")
    results.append(
        (
            "a green, mergeable pull request on the group branch is merged at its head",
            got["merged"] == [11]
            and len(merge) == 1
            and HEAD in merge[0]
            and "--match-head-commit" in merge[0]
            and "--admin" not in merge[0]
            and listing[0][listing[0].index("--base") + 1] == BRANCH,
            f"got={got} merge={merge}",
        )
    )
    held_cases: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
        ("a fork", _pr(11, isCrossRepository=True), _checks("pass"), "from a fork"),
        ("a draft", _pr(11, isDraft=True), _checks("pass"), "draft"),
        (
            "a conflict",
            _pr(11, mergeable="CONFLICTING"),
            _checks("pass"),
            "conflicting",
        ),
        (
            "an unknown merge state",
            _pr(11, mergeable="UNKNOWN"),
            _checks("pass"),
            "unknown",
        ),
        (
            "a blocking review",
            _pr(11, reviewDecision="CHANGES_REQUESTED"),
            _checks("pass"),
            "changes",
        ),
        ("red checks", _pr(11), _checks("pass", "fail"), "checks failing: job2"),
        ("running checks", _pr(11), _checks("pending"), "checks pending: job1"),
        ("no checks", _pr(11), {"returncode": 1}, "checks unknown"),
    ]
    for label, pr, checks, reason in held_cases:
        routes = {"pr list": _listing(pr), "pr checks": checks, "pr merge": {}}
        with _RoutedGH(routes) as gh:
            got = merge_fix_pull_requests(BRANCH, ref)
        held = got["held"]
        results.append(
            (
                f"a pull request that is {label} is held, not merged",
                got["merged"] == []
                and not gh.ran("pr merge")
                and len(held) == 1
                and reason in held[0]["reason"],
                f"got={got}",
            )
        )
    with _RoutedGH({**green, "pr merge": {"returncode": 1}}) as gh:
        got = merge_fix_pull_requests(BRANCH, ref)
    results.append(
        (
            "a merge gh refuses is held with its reason",
            got["merged"] == []
            and got["held"] == [{"number": 11, "reason": "gh exited 1"}],
            f"got={got}",
        )
    )
    with _RoutedGH({"pr list": {"returncode": 1}}):
        got = merge_fix_pull_requests(BRANCH, ref)
    results.append(
        (
            "a listing GitHub cannot answer is unknown, never 'nothing open'",
            got["merged"] == []
            and got["held"] == []
            and got["unknown"] == "gh exited 1",
            f"got={got}",
        )
    )
    return results


def _gate(base: Path, name: str, published: tuple[str, ...] = tuple(MEMBERS)) -> Path:
    pub = _receipt_fixture(base, name)
    for member in published:
        _write_receipt(
            pub,
            int(member),
            state="awaiting_review",
            branch=BRANCH,
            group=list(MEMBERS),
            pull_request=URL,
        )
    return pub


def _marker(pub: Path) -> Path:
    return runs_dir(pub) / ".driver" / "group-ready-100030.json"


def _release_checks(base: Path) -> list[Result]:
    results: list[Result] = []
    quiet = {"pr list": _listing(), "pr ready": {}}

    def release(pub: Path, routes: dict[str, dict[str, Any]]) -> tuple[Any, _RoutedGH]:
        with _RoutedGH(routes) as gh:
            return release_group_pull_request(pub, 100030, MEMBERS, URL, BRANCH), gh

    pub = _gate(base, "gate-waiting", published=("100030",))
    got, gh = release(pub, {**quiet, "pr checks": _checks("pass")})
    results.append(
        (
            "a group with a member still to publish waits and readies nothing",
            got["state"] == "WAITING"
            and got["pending"] == ["100020"]
            and not gh.ran("pr ready"),
            f"got={got}",
        )
    )
    pub = _gate(base, "gate-green")
    got, gh = release(pub, {**quiet, "pr checks": _checks("pass", "skipping")})
    again, gh_again = release(pub, {**quiet, "pr checks": _checks("fail")})
    results.append(
        (
            "green checks and no open fix make the pull request ready, once",
            got["state"] == "READY"
            and len(gh.ran("pr ready")) == 1
            and _marker(pub).is_file()
            and again["state"] == "READY"
            and not gh_again.ran("pr ready"),
            f"got={got} again={again}",
        )
    )
    held_cases: list[tuple[str, dict[str, Any], str]] = [
        ("red checks", _checks("pass", "fail"), "checks failing: job2"),
        ("running checks", _checks("pending"), "checks pending: job1"),
        ("checks not reported yet", {"returncode": 1}, "checks unknown"),
    ]
    for label, checks, reason in held_cases:
        pub = _gate(base, f"gate-{label.split()[0]}")
        got, gh = release(pub, {**quiet, "pr checks": checks})
        results.append(
            (
                f"{label} hold the pull request in draft and say why",
                got["state"] == "HELD"
                and got["reasons"] == [reason]
                and not gh.ran("pr ready")
                and not _marker(pub).exists(),
                f"got={got}",
            )
        )
    pub = _gate(base, "gate-fix-merged")
    routes = {
        **quiet,
        "pr list": _listing(_pr(11)),
        "pr checks": _checks("pass"),
        "pr merge": {},
    }
    got, gh = release(pub, routes)
    results.append(
        (
            "a fix merged just now holds ready until the new head has its checks",
            got["state"] == "HELD"
            and got["fix_pull_requests"]["merged"] == [11]
            and not gh.ran("pr ready"),
            f"got={got}",
        )
    )
    pub = _gate(base, "gate-fix-held")
    routes = {
        **quiet,
        "pr list": _listing(_pr(11, isDraft=True)),
        "pr checks": _checks("pass"),
    }
    got, gh = release(pub, routes)
    results.append(
        (
            "a fix pull request left open holds ready and is named",
            got["state"] == "HELD"
            and "#11 (draft)" in got["reasons"][0]
            and not gh.ran("pr ready"),
            f"got={got}",
        )
    )
    pub = _gate(base, "gate-unreadable")
    got, gh = release(pub, {"pr list": {"returncode": 1}, "pr checks": _checks("pass")})
    results.append(
        (
            "pull requests GitHub cannot list hold ready",
            got["state"] == "HELD" and not gh.ran("pr ready"),
            f"got={got}",
        )
    )
    pub = _gate(base, "gate-refused")
    got, gh = release(
        pub, {**quiet, "pr ready": {"returncode": 1}, "pr checks": _checks("pass")}
    )
    results.append(
        (
            "a ready transition gh refuses holds and leaves no marker",
            got["state"] == "HELD"
            and got["reasons"] == ["gh exited 1"]
            and not _marker(pub).exists(),
            f"got={got}",
        )
    )
    return results


def _mode_and_predecessor_checks(base: Path) -> list[Result]:
    results: list[Result] = []
    pub = _gate(base, "gate-mode")
    with _RoutedGH(
        {"pr list": _listing(), "pr checks": _checks("pass"), "pr ready": {}}
    ):
        got = mode_gate(pub, 100020)
    results.append(
        (
            "the gate mode reports the group's state in one summary line",
            got["ok"] is True and got["state"] == "READY" and "READY" in got["summary"],
            f"got={got}",
        )
    )
    alone = _receipt_fixture(base, "gate-alone")
    _write_receipt(alone, 100040, state="awaiting_review", branch="feature/solo")
    got = mode_gate(alone, 100040)
    results.append(
        (
            "a phase outside a group has nothing to gate",
            got["ok"] is True and got["state"] == "NO-GROUP",
            f"got={got}",
        )
    )
    sib = _gate(base, "gate-sibling", published=("100020",))
    own = open_predecessor(sib, 100030, own_branch=BRANCH)
    other = open_predecessor(sib, 100030, own_branch="feature/elsewhere")
    results.append(
        (
            "a member on the phase's own branch is no predecessor; another branch is",
            own is None and isinstance(other, dict) and other["branch"] == BRANCH,
            f"own={own} other={other}",
        )
    )
    return results


def _group_gate_checks(base: Path) -> list[Result]:
    return (
        _verdict_checks()
        + _fix_merge_checks()
        + _release_checks(base)
        + _mode_and_predecessor_checks(base)
    )


__all__ = ["_group_gate_checks"]
