# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A checked no-change close that keeps refusing gets three attempts, then moves on.

The real driver selection, the real hold, the real board, and the real git checks
run. GitHub and the notice channel are fakes, as in the no-change close tests.
"""

from argus.issues.no_change import ATTEMPTS_FILE, CLOSED_FILE
from issues_fixture import fixture_update
from no_change_fixture import DONE, KEY, NEXT, NO_REASON_NOTICE, Case
from no_change_fixture import case as case  # noqa: F401

NEXT_KEY = f"{NEXT:06d}"
GIVE_UP = "could not close it after 3 attempts"


def _refuse(case: Case) -> None:
    """Leave a file in the checkout, so the clean-tree check refuses the close."""
    (case.root / "scratch.txt").write_text("left over\n", encoding="utf-8")


def test_a_refused_close_holds_new_work_before_the_third_attempt(case: Case) -> None:
    # Given a dirty tree, when two ticks run, then the claim holds the queue and the
    # next issue is not reserved.
    _refuse(case)
    assert case.tick() == ""
    assert case.tick() == ""
    assert case.notes() == [NO_REASON_NOTICE]
    assert case.board()[KEY]["status"] == "claimed"
    assert NEXT_KEY not in case.board()


def test_the_third_refusal_raises_one_notice_and_the_queue_moves_on(
    case: Case,
) -> None:
    # Given three refused closes, when the next tick runs, then one Needs You names
    # the cause and the attempts, the claim is kept, and the next issue is reserved.
    _refuse(case)
    for _ in range(3):
        case.pass_time(600)
        case.tick()
    assert case.tick() == str(NEXT)
    assert case.closes() == []
    assert case.board()[KEY]["status"] == "claimed"
    assert len(case.notes()) == 2
    assert case.notes()[0] == NO_REASON_NOTICE
    assert GIVE_UP in case.notes()[1]
    assert "the no-change checks did not pass" in case.notes()[1]


def test_a_spent_claim_starts_no_second_run_and_raises_nothing_more(
    case: Case,
) -> None:
    # Given the attempts are spent, when more ticks run, then the claim never returns
    # for a second run, and the Needs You line is not sent again.
    _refuse(case)
    for _ in range(3):
        case.pass_time(600)
        case.tick()
    assert case.tick() == str(NEXT)
    assert case.tick() != str(DONE)
    assert case.board()[KEY]["status"] == "claimed"
    assert len(case.notes()) == 2


def test_a_failed_close_skips_the_claim_after_the_third_attempt(case: Case) -> None:
    # Given the close keeps failing on GitHub, when three ticks run, then the next tick
    # reserves the next issue and one notice names the attempts.
    fixture_update(case.github, mode="close-failure")
    for _ in range(3):
        case.pass_time(600)
        case.tick()
    assert len(case.closes()) == 3
    assert case.tick() == str(NEXT)
    assert len(case.notes()) == 1
    assert GIVE_UP in case.notes()[0]


def test_a_later_good_close_clears_the_count(case: Case) -> None:
    # Given one failed close, when the close works on the next tick, then the count
    # file is gone and the issue is closed.
    fixture_update(case.github, mode="close-failure")
    case.tick()
    assert (case.run_folder() / ATTEMPTS_FILE).exists()
    fixture_update(case.github, mode=None)
    case.pass_time(600)
    case.tick()
    assert len(case.closes()) == 2
    assert (case.run_folder() / CLOSED_FILE).exists()
    assert not (case.run_folder() / ATTEMPTS_FILE).exists()


def test_an_invalid_count_file_does_not_stop_the_tick(case: Case) -> None:
    # Given an unreadable count, when a tick runs, then the claim counts as spent, the
    # later free issue is reserved, nothing closes, and one notice names the record.
    (case.run_folder() / ATTEMPTS_FILE).write_text("{", encoding="utf-8")
    assert case.tick() == str(NEXT)
    assert case.closes() == []
    assert len(case.notes()) == 1
    assert "the attempt record is unreadable" in case.notes()[0]
    assert case.tick() == ""
    assert len(case.notes()) == 1


def test_a_count_without_an_attempt_list_counts_as_spent(case: Case) -> None:
    # Given a count whose attempts field is not a list, when a tick runs, then the claim
    # counts as spent, the later free issue is reserved, and one notice names the record.
    count = case.run_folder() / ATTEMPTS_FILE
    count.write_text('{"attempts": 5}\n', encoding="utf-8")
    assert case.tick() == str(NEXT)
    assert case.closes() == []
    assert len(case.notes()) == 1
    assert "the attempt record is unreadable" in case.notes()[0]
