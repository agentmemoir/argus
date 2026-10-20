# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read one pull request's checks once, and report what they say.

``gh pr checks`` exits 0 when nothing failed and nothing is pending, and that
exit code is necessary but never sufficient. A skipped, neutral, or cancelled
check is neither failed nor pending, so gh exits 0 while the pull request did
not pass everything. An empty list is not a pass either: a branch with no checks
at all makes gh print "no checks reported" and exit non-zero.

So this mode reads the rows, not only the code. ``ok`` is true only when gh
reported at least one row and every row's bucket is ``pass``. Anything else is
named in ``summary`` and in the row's blockers, so a reader can see what ran,
what did not, and what is unknown.

This mode never waits. It does not pass ``--watch``, it does not poll, and it
does not sleep: one call, one answer, and the caller decides when to look again.
"""

from pathlib import Path
from typing import Any

from argus.gitsync.gh import gh_pull_request_checks, gh_pull_requests_for_branch
from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.git import ConfigError
from argus.gitsync.mode_open_pr import resolve_branch
from argus.gitsync.peek_ci_summary import (
    BUCKETS,
    NEVER_RAN,
    blocker_reasons,
    bucket_counts,
    checks_state,
    summary_line,
    target_label,
)

# The buckets gh reports. Anything else counts toward the row total and toward
# nothing else, which makes it "not a pass" rather than an invisible row.

# How many check names one summary line names before it counts the rest.

# The code gh's helper reports when gh never ran, which is not a gh exit code.


def _number_from_url(url: str) -> int | None:
    """The pull request number gh put at the end of its URL, when there is one."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _target_from(pull_request: int | str) -> tuple[int | str, int | None, str | None]:
    """The argument gh gets, plus the number and URL it already tells us.

    A number is passed as a number and its URL is left unknown rather than
    built by hand from a guess about the host. A URL is passed as it came, and
    its trailing number is read off it.

    Raises:
        ConfigError: The value is neither a number nor a URL, so gh would be
            asked something this mode cannot describe.
    """
    if isinstance(pull_request, int):
        return int(pull_request), int(pull_request), None
    text = str(pull_request).strip()
    if text.isdigit():
        return int(text), int(text), None
    if text.startswith(("http://", "https://")) and "/" in text:
        return text, _number_from_url(text), text
    raise ConfigError(f"--pull-request {text!r} is not a pull request number or URL")


def _row(root: Path) -> dict[str, Any]:
    """One repository's row, with every field present and unclaimed."""
    checks: dict[str, int] = dict.fromkeys(BUCKETS, 0)
    checks["total"] = 0
    return {
        "repo": str(root),
        "ok": False,
        "number": None,
        "url": None,
        "state": "UNKNOWN",
        "checks": checks,
        "blockers": [],
    }


def _branch_or_none(
    root: Path, phase: int | str | None, branch: str | None
) -> str | None:
    """The branch this read is about, when one can be resolved.

    ``--pull-request`` names the pull request by number or URL, so the read is
    well defined even when the checkout cannot say which branch it is on. The
    branch is still resolved best effort and reported, and a null field is the
    honest answer when it cannot be.
    """
    try:
        return resolve_branch(root, phase, branch)
    except ConfigError:
        return None


def _result(
    branch: str | None,
    row: dict[str, Any],
    code: int | None,
    rows: list[dict[str, Any]] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """The mode's result, in the shape every other mode returns.

    ``detail`` is why gh gave no check rows, and it goes into the summary as
    well as the blockers: the summary is the one line a record shows, so it
    must not say "no check rows" while the reason sits somewhere else.
    """
    return {
        "ok": bool(row["ok"]),
        "mode": "peek-ci",
        "branch": branch,
        "state": row["state"],
        "repos": [row],
        "summary": summary_line(row, code, rows or [], detail),
    }


def mode_peek_ci(
    root: str | Path,
    phase: int | str | None = None,
    branch: str | None = None,
    pull_request: int | str | None = None,
) -> dict[str, Any]:
    """Read a pull request's checks once and report the honest answer.

    The pull request is the one ``--pull-request`` names, or else the open pull
    request whose head is the resolved branch: the explicit ``--branch``, else
    the phase's branch, else the branch the checkout is on. An explicit pull
    request is read directly, and the branch beside it is resolved best effort,
    so it is null when the checkout cannot say. No pull request found is a
    result with a reason, not a crash.

    Returns ``ok``, ``mode``, ``branch``, ``state``, ``repos``, and ``summary``.
    Each repository row carries ``repo``, ``ok``, ``number``, ``url``, ``state``,
    and ``checks`` with one count per bucket plus ``total``.
    """
    root = Path(root).resolve()
    row = _row(root)
    target: int | str
    branch_name: str | None = None
    number: int | None = None
    url: str | None = None

    if pull_request is not None:
        target, number, url = _target_from(pull_request)
        branch_name = _branch_or_none(root, phase, branch)
    else:
        branch_name = resolve_branch(root, phase, branch)
        found, why = gh_pull_requests_for_branch(root, branch_name)
        if found is None:
            row["blockers"] = [
                f"could not list pull requests for {branch_name!r}: {why}"
            ]
            return _result(branch_name, row, None, detail=why)
        open_rows = [item for item in found if item["state"] == "OPEN"]
        if not open_rows:
            if found:
                newest = max(found, key=lambda item: int(item["number"]))
                row["number"] = int(newest["number"])
                row["url"] = newest["url"]
                row["blockers"] = [
                    f"no open pull request for {branch_name!r}; the newest is "
                    f"{newest['state']}, #{int(newest['number'])}, so no checks "
                    "are in flight"
                ]
            else:
                row["blockers"] = [
                    f"no pull request for {branch_name!r}; no checks to read"
                ]
            return _result(branch_name, row, None)
        chosen = max(open_rows, key=lambda item: int(item["number"]))
        number = int(chosen["number"])
        url = chosen["url"]
        target = number

    rows, code, detail = gh_pull_request_checks(root, target)
    row["number"] = number
    row["url"] = url
    if rows is None:
        row["blockers"] = [
            f"gh could not report checks for {target_label(number, url)}: {detail}"
        ]
        return _result(branch_name, row, None if code < 0 else code, detail=detail)

    counts = bucket_counts(rows)
    state = checks_state(rows, counts)
    row["checks"] = counts
    row["state"] = state
    if state == "PASSING" and code in (0, NEVER_RAN):
        row["ok"] = True
        return _result(branch_name, row, code, rows)
    if state == "PASSING":
        # gh printed all-pass rows and did not exit 0. The rows and the code
        # disagree, so neither is trusted: the state is unknown, not passing.
        row["state"] = "UNKNOWN"
        row["blockers"] = [
            f"{gh_exit_detail(code, 'the checks read')}; every row said pass"
        ]
        return _result(branch_name, row, code, rows)
    row["blockers"] = blocker_reasons(rows, counts, code)
    return _result(branch_name, row, code, rows)


__all__ = ["mode_peek_ci"]
