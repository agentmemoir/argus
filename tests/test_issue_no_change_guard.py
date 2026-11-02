# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""What the public closing comment may hold: the reason passes the guard, or it goes."""

import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.issues.no_change import close_no_change, closing_comment, publishable
from no_change_fixture import DONE, REASON, Case
from no_change_fixture import case as case  # noqa: F401


def _host_check(case: Case) -> None:
    """A host's reply check, in this process: it refuses one host word."""
    check = case.config.parent / "reply_check.py"
    check.write_text(
        "import sys\nsys.exit(1 if 'hostword' in sys.stdin.read() else 0)\n", "utf-8"
    )
    conventions.override({"commands.reply-check": f"{sys.executable} {check}"})


def _close(case: Case, reason: str) -> str:
    """The real close against the fake GitHub; return the comment it posted."""
    outcome = close_no_change(case.root, case.run_folder(), DONE, reason, "worker-one")
    assert outcome == "closed"
    row = case.closes()[0]
    return row[row.index("--comment") + 1]


def test_a_reason_that_passes_the_host_check_is_marked_as_a_claim(case: Case) -> None:
    _host_check(case)
    assert f"Argus did not check it: `{REASON}`" in _close(case, REASON)


def test_a_reason_the_host_check_refuses_is_not_posted(case: Case) -> None:
    # Given the token scan passes a machine name, when the host's check refuses the
    # reason, then the issue still closes, and the comment holds the fixed list only.
    _host_check(case)
    comment = _close(case, "Already done on hostword.")
    assert "hostword" not in comment
    assert "The agent's reason is not shown" in comment


def test_hostile_text_stays_one_inert_code_span(case: Case) -> None:
    _host_check(case)
    hostile = "$(touch canary) `touch canary` @someone <!-- x --> [a](https://x.test)"
    span = hostile.replace("`", "'")
    assert f"Argus did not check it: `{span}`\n" in _close(case, hostile)


def test_hostile_text_stays_inert_through_the_shell(case: Case) -> None:
    canary = case.root / "canary"
    hostile = "$(touch canary) `touch canary`; touch canary"
    (case.run_folder() / "no-change.txt").write_text(f"{hostile}\n", "utf-8")
    case.tick()
    assert len(case.closes()) == 1
    assert not canary.exists()
    assert "touch canary" not in case.closes()[0][-1]


@pytest.mark.parametrize(
    "reason",
    [
        "Seen on worker-one.",
        "Read /var/lib/project/file.txt first.",
        "Read ~/project/file.txt first.",
    ],
)
def test_a_private_looking_reason_is_not_publishable(case: Case, reason: str) -> None:
    _host_check(case)
    assert not publishable(reason, "worker-one")


def test_the_machines_own_names_are_refused(
    case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_check(case)
    monkeypatch.setattr("socket.gethostname", lambda: "buildbox.example")
    assert not publishable("It ran on BuildBox.", "worker-one")
    monkeypatch.setattr(Path, "home", lambda: Path("/somewhere/owner-name"))
    assert not publishable("Done by owner-name.", "worker-one")


@pytest.mark.usefixtures("case")
def test_a_host_with_no_check_posts_no_reason() -> None:
    assert not publishable(REASON, "worker-one")


@pytest.mark.usefixtures("case")
def test_a_check_that_cannot_run_posts_no_reason() -> None:
    conventions.override({"commands.reply-check": "/nonexistent/check"})
    assert not publishable(REASON, "worker-one")


def test_a_plain_reason_with_a_repository_path_is_publishable(case: Case) -> None:
    _host_check(case)
    assert publishable(REASON, "worker-one")
    assert publishable("See https://example.com/a/b.", "worker-one")


def test_the_comment_marks_the_claim_and_ends_in_its_marker() -> None:
    body = closing_comment("a `b` c", "<!-- m -->", show_reason=True)
    assert "`a 'b' c`" in body
    assert body.endswith("<!-- m -->")
