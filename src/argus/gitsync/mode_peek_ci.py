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
from argus.gitsync.git import ConfigError
from argus.gitsync.mode_open_pr import resolve_branch

# The buckets gh reports. Anything else counts toward the row total and toward
# nothing else, which makes it "not a pass" rather than an invisible row.
BUCKETS = ("pass", "fail", "pending", "skipping", "cancel")

# How many check names one summary line names before it counts the rest.
MAX_NAMED = 5

# The code gh's helper reports when gh never ran, which is not a gh exit code.
NEVER_RAN = -1


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


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """One count per bucket gh reports, plus the number of rows."""
    counts = dict.fromkeys(BUCKETS, 0)
    counts["total"] = len(rows)
    for row in rows:
        bucket = str(row.get("bucket") or "")
        if bucket in BUCKETS:
            counts[bucket] += 1
    return counts


def _state(rows: list[dict[str, Any]], counts: dict[str, int]) -> str:
    """The one word for this pull request's checks.

    ``passing`` needs a row to exist and every row to be a pass. A fail bucket
    outranks a pending one, because a caller can act on a failure now. A
    skipping or cancelled row, or a bucket gh did not report as one of the five,
    is ``unknown``: it is not a failure to fix and it is not a pass to trust.
    """
    if not rows:
        return "unknown"
    if counts["fail"]:
        return "failing"
    if counts["pending"]:
        return "pending"
    if counts["skipping"] or counts["cancel"]:
        return "unknown"
    if counts["pass"] == len(rows):
        return "passing"
    return "unknown"


def _shortened(names: list[str]) -> str:
    """The names joined for one line, shortened to a readable count."""
    shown = ", ".join(names[:MAX_NAMED])
    if len(names) > MAX_NAMED:
        shown = f"{shown}, and {len(names) - MAX_NAMED} more"
    return shown


def _named(rows: list[dict[str, Any]], buckets: tuple[str, ...]) -> str:
    """The names of the checks in ``buckets``, shortened to a readable count."""
    return _shortened(
        [
            str(row.get("name") or "unnamed")
            for row in rows
            if str(row.get("bucket") or "") in buckets
        ]
    )


def _unknown_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows whose bucket this mode does not recognise.

    gh may add a bucket at any time, and a row it reports in a bucket this mode
    does not know is not a pass. Its name is kept so the summary and the
    blockers can name it rather than only count it.
    """
    return [row for row in rows if str(row.get("bucket") or "") not in BUCKETS]


def _unknown_count(counts: dict[str, int]) -> int:
    """How many rows sit in a bucket this mode does not recognise.

    One remainder for the summary and the blockers to share, so the two can
    never report different counts for the same rows.
    """
    return counts["total"] - sum(counts[bucket] for bucket in BUCKETS)


def _unknown_labels(rows: list[dict[str, Any]]) -> list[str]:
    """Each unrecognised row as ``name (bucket)``, so the bucket is named too."""
    return [
        f"{row.get('name') or 'unnamed'} ({row.get('bucket') or 'no bucket'})"
        for row in _unknown_rows(rows)
    ]


def _row(root: Path) -> dict[str, Any]:
    """One repository's row, with every field present and unclaimed."""
    checks = dict.fromkeys(BUCKETS, 0)
    checks["total"] = 0
    return {
        "repo": str(root),
        "ok": False,
        "number": None,
        "url": None,
        "state": "unknown",
        "checks": checks,
        "blockers": [],
    }


def _reasons(
    rows: list[dict[str, Any]], counts: dict[str, int], code: int
) -> list[str]:
    """Why these checks are not a pass, one short line per fact."""
    total = counts["total"]
    if total == 0:
        return [
            f"gh reported no check rows; an empty list is not a pass (gh exited {code})"
        ]
    reasons: list[str] = []
    if counts["fail"]:
        reasons.append(
            f"{counts['fail']} of {total} checks failed: {_named(rows, ('fail',))}"
        )
    if counts["pending"]:
        reasons.append(
            f"{counts['pending']} of {total} checks are still pending: "
            f"{_named(rows, ('pending',))}; peek-ci does not wait"
        )
    if counts["skipping"]:
        reasons.append(
            f"{counts['skipping']} of {total} checks were skipped and are not "
            f"passes: {_named(rows, ('skipping',))}"
        )
    if counts["cancel"]:
        reasons.append(
            f"{counts['cancel']} of {total} checks were cancelled and are not "
            f"passes: {_named(rows, ('cancel',))}"
        )
    other = _unknown_count(counts)
    if other:
        reasons.append(
            f"{other} of {total} checks have a bucket this mode does not know: "
            f"{_shortened(_unknown_labels(rows))}; they are not passes"
        )
    if not reasons:
        reasons.append(f"gh exited {code}, which is not a passing check list")
    return reasons


def _summary(row: dict[str, Any], code: int | None, rows: list[dict[str, Any]]) -> str:
    """What ran, what did not, and what is unknown, in one line.

    A skipped or cancelled check is named here and not only counted, because a
    reader who sees the total alone cannot tell which check did not run. The
    same holds for a bucket this mode does not recognise: it is counted in the
    total, so it must be named, or the line would leave a row unexplained.
    """
    checks = row["checks"]
    total = checks["total"]
    parts = [f"{row['repo']}: {row['state']}"]
    if total == 0:
        parts.append("no check rows")
    else:
        parts.append(f"{checks['pass']} of {total} pass")
        for bucket in ("fail", "pending", "skipping", "cancel"):
            if not checks[bucket]:
                continue
            label = f"{checks[bucket]} {bucket}"
            named = _named(rows, (bucket,))
            parts.append(f"{label} ({named})" if named else label)
        unknown = _unknown_count(checks)
        if unknown:
            parts.append(
                f"{unknown} unknown bucket ({_shortened(_unknown_labels(rows))})"
            )
    if code is not None:
        parts.append(f"gh exited {code}")
    head = f"peek-ci sync {'ok' if row['ok'] else 'BLOCKED'}"
    return f"{head}: " + "; ".join(parts)


def _target_label(number: int | None, url: str | None) -> str:
    """How to name the pull request in a message, and never ``#None``.

    A URL whose last segment is not a number, such as one that ends in
    ``/files``, gives no number to read, so the URL itself is the name.
    """
    if number is not None:
        return f"#{number}"
    if url:
        return url
    return "the named pull request"


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
) -> dict[str, Any]:
    """The mode's result, in the shape every other mode returns."""
    return {
        "ok": bool(row["ok"]),
        "mode": "peek-ci",
        "branch": branch,
        "state": row["state"],
        "repos": [row],
        "summary": _summary(row, code, rows or []),
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
            return _result(branch_name, row, None)
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
            f"gh could not report checks for {_target_label(number, url)}: {detail}"
        ]
        return _result(branch_name, row, None if code < 0 else code)

    counts = _counts(rows)
    state = _state(rows, counts)
    row["checks"] = counts
    row["state"] = state
    if state == "passing" and code in (0, NEVER_RAN):
        row["ok"] = True
        return _result(branch_name, row, code, rows)
    if state == "passing":
        # gh printed all-pass rows and did not exit 0. The rows and the code
        # disagree, so neither is trusted: the state is unknown, not passing.
        row["state"] = "unknown"
        row["blockers"] = [f"gh exited {code} while every row said pass"]
        return _result(branch_name, row, code, rows)
    row["blockers"] = _reasons(rows, counts, code)
    return _result(branch_name, row, code, rows)


__all__ = ["mode_peek_ci"]
