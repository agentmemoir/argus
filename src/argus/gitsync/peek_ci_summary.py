# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The peek-ci summary: buckets counted, and the words that name them."""

from typing import Any

BUCKETS = ("pass", "fail", "pending", "skipping", "cancel")


MAX_NAMED = 5


NEVER_RAN = -1


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
