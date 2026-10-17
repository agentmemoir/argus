# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair step checks: when a run starts, when the line holds, and when it gives up."""

import json
from pathlib import Path
from typing import Any

from argus.conventions import runs_dir
from argus.gitsync.git import current_branch, git
from argus.gitsync.repair import mode_repair
from argus.gitsync.repair_task import failure_excerpt
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _configure_self_test,
    _init_remote,
)
from argus.gitsync.selftest_gate_fixture import URL, _checks, _RoutedGH
from argus.gitsync.selftest_group_review import _write_receipt

Result = tuple[str, object, str]
BRANCH = "feature/group-cache-warm"
STAMP = "2026-10-12T14:45:16.1234567Z "
LOG = (
    f"job a\tbuild\t{STAMP}noise before\n"
    f"job a\tbuild\t{STAMP}\x1b[31merror: cause of a\x1b[0m\n"
    f"job a\tbuild\t{STAMP}##[error]Process completed with exit code 1.\n"
    f"job b\ttest\t{STAMP}only noise\n"
    f"job b\ttest\t{STAMP}the last line of b\n"
)
OPEN = {"stdout": json.dumps({"state": "OPEN", "headRefOid": "abc123"})}


def _excerpt_checks() -> list[Result]:
    got = failure_excerpt(LOG, before=1)
    capped = failure_excerpt(LOG * 40, before=1, limit=1)
    return [
        (
            "the excerpt keeps the line before each error marker, without stamps or colour",
            "error: cause of a" in got
            and "noise before" not in got
            and STAMP.strip() not in got
            and "\x1b" not in got,
            got,
        ),
        (
            "a job with no marker keeps its last lines",
            "the last line of b" in got and "only noise" not in got,
            got,
        ),
        (
            "every job keeps a share when the excerpt is capped",
            capped.count("### ") == 2,
            capped[:200],
        ),
    ]


def _fixture(base: Path, name: str, machine: str = "self-test") -> Path:
    """A checkout on main, a pushed group branch, and one receipt awaiting review."""
    folder = base / f"repair-{name}"
    folder.mkdir()
    _remote, pub = _init_remote(folder, "pub")
    git(pub, "checkout", "-q", "-b", BRANCH)
    (pub / "f.txt").write_text("x\n")
    git(pub, "add", "f.txt")
    git(pub, "commit", "-q", "-m", "work")
    git(pub, "push", "-q", "-u", "origin", BRANCH)
    git(pub, "checkout", "-q", "main")
    _configure_self_test(pub, pub / BOARD_REL)
    _write_receipt(
        pub,
        100030,
        state="awaiting_review",
        branch=BRANCH,
        pull_request=URL,
        reservation={"machine_id": machine},
    )
    return pub


def _record(pub: Path) -> Path:
    return runs_dir(pub) / ".driver" / "repair" / "pr-7.json"


def _routes(
    *buckets: str,
    log: str | None = LOG,
    pr: dict[str, Any] = OPEN,
    checks: str | None = None,
    scan: str | None = None,
) -> dict[str, Any]:
    run: dict[str, Any] = {"returncode": 1} if log is None else {"stdout": log}
    routes: dict[str, Any] = {
        "pr view": pr,
        "pr checks": {"stdout": checks} if checks is not None else _checks(*buckets),
        "run view": run,
    }
    if scan is not None:
        routes["api --paginate"] = {"stdout": scan}
    return routes


def _ask(pub: Path, routes: dict[str, Any]) -> tuple[dict[str, Any], _RoutedGH]:
    with _RoutedGH(routes) as gh:
        return mode_repair(pub), gh


def _repair_checks(base: Path) -> list[Result]:
    results: list[Result] = []
    pub = _fixture(base, "red")
    got, gh = _ask(pub, _routes("pass", "fail"))
    task = Path(got.get("task", "/nowhere"))
    text = task.read_text() if task.is_file() else ""
    results.append(
        (
            "checks that finished red start a repair on the pull request's branch",
            got["state"] == "REPAIR"
            and got["id"] == "7-1"
            and got["attempt"] == 1
            and current_branch(pub) == BRANCH
            and "Failing checks: job2" in text
            and "error: cause of a" in text
            and (task.parent / "failed.log").is_file(),
            f"got={got}",
        )
    )
    results.append(
        (
            "a round is written at once and counted when the next turn reads its scope",
            json.loads(_record(pub).read_text())["rounds"] == 1
            and json.loads(_record(pub).read_text())["attempts"] == 0,
            _record(pub).read_text(),
        )
    )
    results.append(
        (
            "a workflow failure reads no alerts, so the common case costs no call",
            gh.ran("run view") and not gh.ran("api --paginate"),
            f"calls={[call[1:3] for call in gh.calls]}",
        )
    )
    again, _ = _ask(pub, _routes("pass", "fail"))
    results.append(
        (
            "a second red result counts the first round and prepares the second",
            again["state"] == "REPAIR"
            and again["id"] == "7-2"
            and json.loads(_record(pub).read_text())["attempts"] == 1,
            f"again={again}",
        )
    )
    third, _ = _ask(pub, _routes("pass", "fail"))
    quiet, _ = _ask(pub, _routes("pass", "fail"))
    results.append(
        (
            "at the limit the pull request is given up once, and the line goes on",
            third["state"] == "EXHAUSTED"
            and third["notify"] is True
            and quiet["state"] == "EXHAUSTED"
            and quiet["notify"] is False
            and "7-3" not in str(_record(pub).parent.glob("*")),
            f"third={third} quiet={quiet}",
        )
    )
    green, _ = _ask(pub, _routes("pass", "pass"))
    results.append(
        (
            "green checks clear the record, so a later failure starts fresh",
            green["state"] == "NONE" and not _record(pub).exists(),
            f"green={green}",
        )
    )
    return results


def _hold_checks(base: Path) -> list[Result]:
    results: list[Result] = []
    pub = _fixture(base, "running")
    for label, buckets in (
        ("running checks", ("pass", "pending")),
        ("a failure beside a running check", ("fail", "pending")),
    ):
        got, gh = _ask(pub, _routes(*buckets))
        results.append(
            (
                f"{label} hold the line and start nothing",
                got["state"] == "HOLD" and not gh.ran("run view"),
                f"got={got}",
            )
        )
    record = json.loads(_record(pub).read_text())
    record["waiting_since"] = 0.0
    _record(pub).write_text(json.dumps(record))
    expired, _ = _ask(pub, _routes("pending"))
    results.append(
        (
            "a hold ends after repair.wait, and the line goes on",
            expired["state"] == "NONE",
            f"expired={expired}",
        )
    )
    return results


def _skip_checks(base: Path) -> list[Result]:
    results: list[Result] = []
    pub = _fixture(base, "other", machine="another-host")
    got, gh = _ask(pub, _routes("fail"))
    results.append(
        (
            "a pull request another machine published is left alone",
            got["state"] == "NONE" and not gh.calls,
            f"got={got}",
        )
    )
    pub = _fixture(base, "merged")
    merged = {"stdout": json.dumps({"state": "MERGED", "headRefOid": "abc123"})}
    got, gh = _ask(pub, _routes("fail", pr=merged))
    results.append(
        (
            "a merged pull request is not repaired",
            got["state"] == "NONE" and not gh.ran("run view"),
            f"got={got}",
        )
    )
    pub = _fixture(base, "dirty")
    git(pub, "checkout", "-q", "main")
    (pub / "a.txt").write_text("edited\n")
    got, _ = _ask(pub, _routes("fail"))
    results.append(
        (
            "uncommitted work blocks the switch and costs no attempt",
            got["state"] == "BLOCKED" and not _record(pub).exists(),
            f"got={got}",
        )
    )
    pub = _fixture(base, "nolog")
    got, _ = _ask(pub, _routes("fail", log=None))
    results.append(
        (
            "an unreadable failure log blocks the repair and costs no attempt",
            got["state"] == "BLOCKED" and not _record(pub).exists(),
            f"got={got}",
        )
    )
    return results


def _repair_step_checks(base: Path) -> list[Result]:
    return (
        _excerpt_checks()
        + _repair_checks(base)
        + _hold_checks(base)
        + _skip_checks(base)
    )


__all__ = [
    "_ask",
    "_fixture",
    "_record",
    "_repair_step_checks",
    "_routes",
]
