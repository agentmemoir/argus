# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""How a gitsync result is built and how it is written on one line.

The result builders and the summary line live together here, apart from the git
primitives that produce the facts they report: a mode builds a result, and the
summary is what a caller logs.
"""

from typing import Any

# git names the local user and machine in ``'<user>@<host>.(none)'`` when it
# cannot find an identity, so its stderr is read for a known cause and then
# dropped: SECURITY.md rule 4 forbids storing a machine name in a warning.
_NO_IDENTITY_HINTS = (
    "unable to auto-detect email",
    "please tell me who you are",
    "empty ident",
)

# A degraded part carries the start of its warning into a log line, so the
# excerpt is bounded: a git message can be many lines long.
WARNING_EXCERPT_CHARS = 120


def git_failure(step: str, rc: int, stderr: str) -> str:
    """Name a failed git step by step and exit code, never by its stderr.

    ``stderr`` is read for the one cause worth naming to a reader, a checkout
    with no git identity, and is not carried into the result.
    """
    if any(hint in stderr.lower() for hint in _NO_IDENTITY_HINTS):
        return f"{step} failed (git exit {rc}): git has no user identity"
    return f"{step} failed (git exit {rc})"


def blocked(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    info = dict(info)
    info["ok"] = False
    info["action"] = "blocked"
    blockers = list(info.get("blockers", []))
    blockers.append(reason)
    info["blockers"] = blockers
    info.update(extra)
    return info


def degraded(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    """Report a step that did not happen, and let the caller continue.

    This is the repair form of :func:`blocked`: the work is reported in
    ``warning`` so the next tick can retry it, and ``ok`` stays true so no
    caller halts the line. A narrow lost-work stop still uses ``blocked``.

    ``ok`` staying true is the retry design, not a claim of success: the
    warning is carried into :func:`summarize`, so a line that continues is
    logged as ``DEGRADED`` and names the step that was deferred.
    """
    info = dict(info)
    info["ok"] = True
    info["action"] = "continued"
    info["warning"] = reason
    info.update(extra)
    return info


def config_error(mode: str, exc: Exception) -> dict[str, Any]:
    """The result for a usage or environment error: a stop, not a sync outcome."""
    return {
        "ok": False,
        "mode": mode,
        "state": "CONFIG_ERROR",
        "error": str(exc),
    }


def warning_excerpt(warning: str) -> str:
    """The first line of a warning, cut to a length one log line can hold."""
    first = warning.splitlines()[0].strip() if warning.splitlines() else ""
    if len(first) <= WARNING_EXCERPT_CHARS:
        return first
    return first[:WARNING_EXCERPT_CHARS].rstrip() + "..."


def summarize(results: list[dict[str, Any]], mode: str) -> str:
    """One line per repo, and a head word that tells a continued line from a clean one.

    A degraded result keeps ``ok`` true so the next tick retries it, which by
    itself reads exactly like a clean line. Both the head word and the repo's
    own part therefore say so: the head is ``DEGRADED`` and the part carries
    the start of the warning.
    """
    parts: list[str] = []
    for r in results:
        # A stop is printed as a stop, even when the same result also carries a
        # deferred step: its blockers are the news, the warning is not.
        if not r.get("ok"):
            parts.append(f"{r['repo']}: BLOCKED ({'; '.join(r.get('blockers', []))})")
            continue
        warning = str(r.get("warning") or "")
        if warning:
            excerpt = warning_excerpt(warning)
            parts.append(
                f"{r['repo']}: continued ({excerpt})"
                if excerpt
                else f"{r['repo']}: continued"
            )
        else:
            parts.append(f"{r['repo']}: {r.get('action', 'ok')}")
    # A blocked result outranks a degraded one: nothing it deferred is worth
    # reporting next to a stop.
    if not all(r.get("ok") for r in results):
        verdict = "BLOCKED"
    elif any(r.get("warning") for r in results):
        verdict = "DEGRADED"
    else:
        verdict = "ok"
    return f"{mode} sync {verdict}: " + "; ".join(parts)


__all__ = [
    "blocked",
    "config_error",
    "degraded",
    "git_failure",
    "summarize",
    "warning_excerpt",
]
