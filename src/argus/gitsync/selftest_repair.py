# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair step checks: when a run starts, when the line holds, and when it gives up."""

import json
from pathlib import Path
from typing import Any

from argus.conventions import runs_dir
from argus.gitsync.git import current_branch, git
from argus.gitsync.repair import failure_excerpt, mode_repair
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
# A code-scanning check: its link is the scan result, not an Actions run, so
# nothing can be read with `gh run view`. Its failure is written as alerts.
SCAN_LINK = "https://github.com/example-org/example-repo/runs/5150"
SCAN_ROW = {"name": "CodeQL", "bucket": "fail", "link": SCAN_LINK}
SCAN_ALERTS = {
    "number": 9,
    "html_url": "https://github.com/example-org/example-repo/security/code-scanning/9",
    "rule": {
        "id": "rust/hard-coded-cryptographic-value",
        "security_severity_level": "critical",
    },
    "most_recent_instance": {"location": {"path": "src/keys.rs", "start_line": 42}},
}


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
    got, _ = _ask(pub, _routes("pass", "fail"))
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
            "the attempt is counted when the task is made",
            json.loads(_record(pub).read_text())["attempts"] == 1,
            _record(pub).read_text(),
        )
    )
    again, _ = _ask(pub, _routes("pass", "fail"))
    results.append(
        (
            "a second red result prepares the second attempt",
            again["state"] == "REPAIR" and again["id"] == "7-2",
            f"again={again}",
        )
    )
    last, _ = _ask(pub, _routes("pass", "fail"))
    quiet, _ = _ask(pub, _routes("pass", "fail"))
    results.append(
        (
            "at the limit the pull request is given up once, and the line goes on",
            last["state"] == "EXHAUSTED"
            and last["notify"] is True
            and quiet["state"] == "EXHAUSTED"
            and quiet["notify"] is False
            and "7-3" not in str(_record(pub).parent.glob("*")),
            f"last={last} quiet={quiet}",
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


def _scan_checks(base: Path) -> list[Result]:
    """A code-scanning check has no run log, so its open alerts are the evidence."""
    results: list[Result] = []
    rows = json.dumps([SCAN_ROW])
    pub = _fixture(base, "scan")
    got, gh = _ask(pub, _routes(checks=rows, scan=json.dumps([[SCAN_ALERTS]])))
    task = Path(got.get("task", "/nowhere"))
    text = task.read_text() if task.is_file() else ""
    results.append(
        (
            "a code-scanning failure is repaired from its alerts, with no run log read",
            got["state"] == "REPAIR"
            and got["id"] == "7-1"
            and not gh.ran("run view")
            and "rust/hard-coded-cryptographic-value" in text
            and "src/keys.rs:42" in text,
            f"got={got}",
        )
    )
    results.append(
        (
            "the alert evidence is kept beside the task and the attempt is counted",
            (task.parent / "failed.log").is_file()
            and json.loads(_record(pub).read_text())["attempts"] == 1,
            _record(pub).read_text(),
        )
    )
    pub = _fixture(base, "scan-mixed")
    log_row = {
        "name": "job1",
        "bucket": "fail",
        "link": "https://github.com/example-org/example-repo/actions/runs/4242/job/1",
    }
    got, gh = _ask(
        pub,
        _routes(
            checks=json.dumps([log_row, SCAN_ROW]),
            scan=json.dumps([[SCAN_ALERTS]]),
        ),
    )
    task = Path(got.get("task", "/nowhere"))
    text = task.read_text() if task.is_file() else ""
    results.append(
        (
            "a workflow failure and a code-scanning failure both reach the task",
            got["state"] == "REPAIR"
            and gh.ran("run view")
            and "error: cause of a" in text
            and "rust/hard-coded-cryptographic-value" in text,
            f"got={got}",
        )
    )
    pub = _fixture(base, "scan-quiet")
    got, _ = _ask(pub, _routes(checks=rows, scan=json.dumps([[]])))
    results.append(
        (
            "a code-scanning failure with no open alert blocks, and names the check",
            got["state"] == "BLOCKED"
            and "CodeQL" in str(got["summary"])
            and not _record(pub).exists(),
            f"got={got}",
        )
    )
    return results


def _repair_step_checks(base: Path) -> list[Result]:
    return (
        _excerpt_checks()
        + _repair_checks(base)
        + _scan_checks(base)
        + _hold_checks(base)
        + _skip_checks(base)
    )


__all__ = ["_repair_step_checks"]
