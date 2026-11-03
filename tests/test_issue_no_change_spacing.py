# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A no-change close that fails waits: its attempts run now, after 10 minutes, and after 20.

The real driver selection and the real close run, with GitHub as a fake. Time passes
for the attempt record through pass_time, so no test sleeps.
"""

import json

from argus.issues.no_change import ATTEMPTS_FILE
from issues_fixture import fixture_update
from no_change_fixture import Case
from no_change_fixture import case as case  # noqa: F401

GIVE_UP = "could not close it after 3 attempts"


def _counted(case: Case) -> int:
    count = case.run_folder() / ATTEMPTS_FILE
    if not count.exists():
        return 0
    return len(json.loads(count.read_text("utf-8"))["attempts"])


def test_a_second_close_waits_until_ten_minutes_after_the_first(case: Case) -> None:
    # Given a close that keeps failing, when ticks run before ten minutes have passed,
    # then no second close runs and no attempt is counted. At ten minutes it runs.
    fixture_update(case.github, mode="close-failure")
    case.tick()
    assert len(case.closes()) == 1
    assert _counted(case) == 1
    case.tick()
    case.pass_time(540)
    case.tick()
    assert len(case.closes()) == 1
    assert _counted(case) == 1
    case.pass_time(60)
    case.tick()
    assert len(case.closes()) == 2
    assert _counted(case) == 2
    assert case.notes() == []


def test_the_third_close_is_due_at_twenty_minutes_then_one_notice(case: Case) -> None:
    # Given two failed closes, when ticks run before twenty minutes have passed from the
    # first, then no close runs. At twenty minutes the third runs and raises one notice.
    fixture_update(case.github, mode="close-failure")
    case.tick()
    case.pass_time(600)
    case.tick()
    case.pass_time(540)
    case.tick()
    assert len(case.closes()) == 2
    assert _counted(case) == 2
    assert case.notes() == []
    case.pass_time(60)
    case.tick()
    assert len(case.closes()) == 3
    assert len(case.notes()) == 1
    assert GIVE_UP in case.notes()[0]
    case.tick()
    assert len(case.closes()) == 3
    assert len(case.notes()) == 1


def test_a_refused_check_waits_until_ten_minutes_after_the_first(case: Case) -> None:
    # Given a dirty tree, when a tick runs before ten minutes have passed, then the
    # refusal adds no attempt. At ten minutes the next attempt counts.
    (case.root / "scratch.txt").write_text("left over\n", encoding="utf-8")
    case.tick()
    assert _counted(case) == 1
    case.tick()
    assert _counted(case) == 1
    case.pass_time(600)
    case.tick()
    assert _counted(case) == 2
    assert case.closes() == []
