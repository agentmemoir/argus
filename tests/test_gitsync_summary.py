# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A degraded result is visible in the summary, not only in ``warning``.

``degraded`` keeps ``ok`` true so the next tick retries the step, but the sync
line the driver logs said ``<mode> sync ok`` and ``<repo>: continued``, which
reads exactly like a clean run. The summary now says ``DEGRADED`` and names the
deferred step.
"""

from typing import Any

from argus.gitsync.modes import result_exit_code
from argus.gitsync.report import (
    WARNING_EXCERPT_CHARS,
    blocked,
    degraded,
    summarize,
)

REPO = "/work/board"


def _degraded(reason: str = "checkpoint commit failed (git exit 1)") -> dict[str, Any]:
    return degraded({"repo": REPO}, reason)


def test_a_degraded_result_is_not_reported_as_ok_in_the_summary() -> None:
    out = _degraded()
    line = summarize([out], "clean")
    assert "continued" in line, line
    assert "DEGRADED" in line, line
    assert "sync ok" not in line, line
    # The retry design is unchanged: ok stays true and the exit code stays 0.
    assert out["ok"] is True
    assert result_exit_code(out) == 0


def test_the_degraded_part_names_the_deferred_step() -> None:
    line = summarize([_degraded()], "clean")
    assert "checkpoint commit failed (git exit 1)" in line, line


def test_a_long_warning_is_cut_to_its_first_line() -> None:
    line = summarize([_degraded("first line\nsecond line\nthird line")], "clean")
    assert "first line" in line, line
    assert "second line" not in line, line


def test_a_long_warning_is_cut_to_a_bounded_length() -> None:
    line = summarize([_degraded("x" * (WARNING_EXCERPT_CHARS * 3))], "clean")
    assert "x" * WARNING_EXCERPT_CHARS in line, line
    assert "x" * (WARNING_EXCERPT_CHARS + 1) not in line, line


def test_a_clean_result_still_reads_as_ok() -> None:
    clean = {"repo": REPO, "ok": True, "action": "committed-and-pushed"}
    assert summarize([clean], "clean") == (
        f"clean sync ok: {REPO}: committed-and-pushed"
    )


def test_a_blocked_result_still_reads_as_blocked() -> None:
    stop = blocked({"repo": REPO}, "staged changes exist outside the current phase")
    line = summarize([_degraded(), stop], "clean")
    assert line.startswith("clean sync BLOCKED: "), line
    assert "BLOCKED (staged changes exist outside the current phase)" in line, line


def test_a_blocked_result_that_also_carries_a_warning_keeps_its_blockers() -> None:
    # ``blocked`` copies the dict it is given and neither sets nor clears
    # ``warning``, so a degraded result handed on to it holds both. The stop is
    # the news: the part must print its blockers, not the deferred step.
    both = dict(
        _degraded("checkpoint push failed (git exit 1)"),
        ok=False,
        action="blocked",
        blockers=["staged changes exist outside the current phase"],
    )
    line = summarize([both], "clean")
    assert line.startswith("clean sync BLOCKED: "), line
    assert "BLOCKED (staged changes exist outside the current phase)" in line, line
    assert "continued" not in line, line


def test_an_empty_result_list_still_reads_as_ok() -> None:
    assert summarize([], "clean") == "clean sync ok: "
