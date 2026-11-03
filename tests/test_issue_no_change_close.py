# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A checked no-change run closes its issue, and the queue moves on with no person.

The real driver selection, the real selector, the real hold, the real board on local
Git remotes, and the real git checks run. Only GitHub and the notice channel are fakes,
and recover-own-claim is false.
"""

from argus import conventions
from argus.gitsync.git import current_branch
from argus.gitsync.issue_modes import mode_issue_branch
from argus.issues.no_change import CLOSED_FILE
from issues_fixture import argv, fixture_update
from no_change_fixture import (
    BRANCH,
    DONE,
    FREE,
    KEY,
    NEXT,
    NO_REASON_NOTICE,
    OLD_NOTICE,
    TASK,
    Case,
)
from no_change_fixture import case as case  # noqa: F401


def test_a_checked_run_closes_and_the_second_tick_launches_the_next_issue(
    case: Case,
) -> None:
    # Given a checked no-change run first in the queue, when two ticks run, then the
    # first closes the issue and the second claims the next one, with no notice.
    assert case.tick() == ""
    assert case.notes() == []
    assert case.board()[KEY]["status"] == "claimed"
    closes = case.closes()
    assert len(closes) == 1
    assert closes[0][:7] == [
        "issue",
        "close",
        str(DONE),
        "--repo",
        "example/project",
        "--reason",
        "not planned",
    ]
    comment = closes[0][closes[0].index("--comment") + 1]
    assert "- The issue branch has zero commits past its base." in comment
    assert "- The working tree is clean." in comment
    assert "- No Argus pull request exists for this issue." in comment
    # This config sets no commands.reply-check, so no reason is posted.
    assert "The agent's reason is not shown" in comment
    assert "main.rs" not in comment
    assert "To undo this, reopen the issue. Argus then starts a new run." in comment

    assert case.tick() == str(NEXT)
    assert case.notes() == []
    assert KEY not in case.board()
    assert any(
        line["argv"][-2:] == ["--remove-label", "accepted"]
        and str(DONE) in line["argv"]
        for line in argv(case.github)
    )
    assert not (case.run_folder() / "run.json").exists()
    assert len(case.closes()) == 1


def test_the_next_issue_gets_its_branch_from_the_finished_checkout(case: Case) -> None:
    # Given the checkout still sits on the closed run's local branch, when the next
    # issue is cut, then the cut leaves that branch, which has no upstream.
    case.tick()
    case.tick()
    conventions.configure_from_cli(["--config", str(case.config)])
    task = next((case.vault / "tasks").glob(f"issue-{NEXT:06d}-*.md"))
    result = mode_issue_branch(case.root, NEXT, task)
    assert result["ok"], result
    assert current_branch(case.root) == f"issue/{NEXT}-fix-the-input"


def test_a_reopened_issue_starts_a_new_run_after_the_release(case: Case) -> None:
    # Given Argus closed the issue and released it, when it is reopened, then the next
    # tick claims it, and the earlier close does not repeat.
    case.tick()
    case.tick()
    case.github_says("OPEN", FREE)
    assert case.tick() == str(DONE)
    assert case.board()[KEY]["status"] == "claimed"
    assert len(case.closes()) == 1


def test_a_reopen_before_the_release_starts_a_new_run(case: Case) -> None:
    # Given a person reopens the issue before the next tick releases it, when ticks
    # run, then the run is released, Argus does not close it again, and a tick claims it.
    case.tick()
    case.github_says("OPEN")
    assert case.tick() == str(NEXT)
    assert len(case.closes()) == 1
    assert KEY not in case.board()
    assert not (case.run_folder() / "run.json").exists()
    case.github_says("OPEN", FREE)
    assert case.tick() == str(DONE)
    assert case.notes() == []


def test_a_reopen_while_the_release_fails_does_not_close_it_again(case: Case) -> None:
    # Given the release cannot write the label, when the reopened issue comes back to
    # the hold, then the record of the close stops a second close.
    case.tick()
    case.github_says("OPEN")
    fixture_update(case.github, mode="edit-failure")
    assert case.tick() == ""
    assert (case.run_folder() / CLOSED_FILE).exists()
    assert len(case.closes()) == 1
    assert case.notes() == []
    fixture_update(case.github, mode=None)
    assert case.tick() == str(NEXT)
    assert KEY not in case.board()


def test_an_old_notice_marker_does_not_stop_the_close(case: Case) -> None:
    # Given a run held before this change, with the old notice in its marker, when a
    # tick runs, then the issue closes, and no notice is sent.
    marker = case.state / f"log-last-notice-completed-{DONE}"
    marker.write_text(OLD_NOTICE, encoding="utf-8")
    case.tick()
    assert len(case.closes()) == 1
    assert case.notes() == []
    assert case.tick() == str(NEXT)


def test_a_failed_read_closes_nothing_and_raises_nothing(case: Case) -> None:
    fixture_update(case.github, mode="failure")
    case.hold()
    assert case.closes() == []
    assert case.notes() == []
    assert "waits" in case.log()


def test_an_issue_recorded_by_another_machine_is_not_closed(case: Case) -> None:
    case.shell(
        "PHASE_MACHINE_ID=worker-two\n"
        "stub_verdict() { printf none; }\nISSUE_PR_VERDICT_CMD=stub_verdict\n"
        f"issue_completed_hold {DONE} tasks/{TASK}\n"
    )
    assert case.closes() == []
    assert case.notes() == []


def test_an_issue_already_closed_gets_no_comment_and_no_notice(case: Case) -> None:
    # Given a valid reason and an issue a person closed, when the hold runs, then Argus
    # writes nothing, and the release path frees the claim on the next tick.
    case.github_says("CLOSED")
    case.hold()
    assert case.closes() == []
    assert case.notes() == []
    assert case.tick() == str(NEXT)
    assert case.closes() == []


def test_a_failed_close_retries_then_raises_one_notice_and_one_comment(
    case: Case,
) -> None:
    # Given the close keeps failing after gh posted its comment, when ticks run, then
    # three attempts run with one comment, and one Needs You names the cause and action.
    fixture_update(case.github, mode="close-failure")
    case.tick()
    assert case.notes() == []
    case.pass_time(600)
    case.tick()
    assert case.notes() == []
    case.pass_time(600)
    case.tick()
    case.tick()
    closes = case.closes()
    assert len(closes) == 3
    assert ["--comment" in row for row in closes] == [True, False, False]
    assert case.notes() == [
        f"Needs You. Issue #{DONE} ends with no pull request and no code change, and "
        "Argus could not close it after 3 attempts (command_failed). Action: close the "
        "issue. Check: the claim label is gone after the next tick."
    ]
    # The person closes it, and the release path needs nothing else.
    fixture_update(case.github, mode=None)
    case.github_says("CLOSED")
    # The next issue was reserved on the skip tick, so this tick launches nothing new.
    assert case.tick() == ""
    assert case.board()[f"{NEXT:06d}"]["status"] == "claimed"
    assert KEY not in case.board()


def test_a_close_that_works_on_retry_raises_nothing(case: Case) -> None:
    fixture_update(case.github, mode="close-failure")
    case.tick()
    fixture_update(case.github, mode=None)
    case.pass_time(600)
    case.tick()
    assert [("--comment" in row) for row in case.closes()] == [True, False]
    assert case.notes() == []
    assert case.tick() == str(NEXT)


def test_a_lost_attempt_record_raises_a_notice_and_closes_nothing(case: Case) -> None:
    (case.run_folder() / "no-change-close-attempts.json").write_text("{", "utf-8")
    case.tick()
    assert case.closes() == []
    assert len(case.notes()) == 1
    assert "the attempt record is unreadable" in case.notes()[0]


def test_without_a_reason_the_old_notice_stays_and_nothing_closes(case: Case) -> None:
    (case.run_folder() / "no-change.txt").unlink()
    case.tick()
    assert case.closes() == []
    assert case.notes() == [NO_REASON_NOTICE]


def test_a_dirty_tree_closes_nothing(case: Case) -> None:
    (case.root / "scratch.txt").write_text("left over\n", encoding="utf-8")
    case.tick()
    assert case.closes() == []
    assert case.notes() == [NO_REASON_NOTICE]


def test_a_pull_request_closes_nothing(case: Case) -> None:
    fixture_update(
        case.github,
        branch_pulls=[
            {
                "number": 9,
                "url": "https://github.com/example/project/pull/9",
                "state": "CLOSED",
                "headRefName": BRANCH,
                "isCrossRepository": False,
            }
        ],
    )
    case.tick()
    assert case.closes() == []
    assert case.notes() == []


FAR = "2099-01-01T00:00:00+00:00"


def test_a_lapsed_claim_with_an_old_marker_still_closes_in_one_tick(case: Case) -> None:
    # Given a run held before this change, whose claim window closed and whose label
    # stays, so the selector skips it and the hold never runs, when a tick runs, then
    # the issue closes, the release follows, and the tick claims the next issue.
    assert case.store.expire_stale(now=FAR)
    (case.state / f"log-last-notice-completed-{DONE}").write_text(OLD_NOTICE, "utf-8")
    assert case.tick() == str(NEXT)
    assert len(case.closes()) == 1
    assert case.notes() == []
    assert any(
        line["argv"][-2:] == ["--remove-label", "accepted"]
        and str(DONE) in line["argv"]
        for line in argv(case.github)
    )
    assert not (case.run_folder() / "run.json").exists()


def test_a_lapsed_claim_that_cannot_close_raises_one_notice(case: Case) -> None:
    assert case.store.expire_stale(now=FAR)
    fixture_update(case.github, mode="close-failure")
    for _ in range(4):
        case.pass_time(600)
        case.tick()
    assert len(case.closes()) == 3
    assert len(case.notes()) == 1
    assert "could not close it after 3 attempts" in case.notes()[0]


def test_a_lapsed_claim_with_no_reason_keeps_its_notice_and_closes_nothing(
    case: Case,
) -> None:
    assert case.store.expire_stale(now=FAR)
    (case.run_folder() / "no-change.txt").unlink()
    assert case.tick() == str(NEXT)
    assert case.closes() == []
    assert case.notes() == [
        f"Needs You. Issue #{DONE} stays held by its claim label: its claim expired "
        "with no pull request. Action: remove the claim label to retry."
    ]


def test_a_failed_close_on_a_lapsed_claim_raises_nothing_yet(case: Case) -> None:
    assert case.store.expire_stale(now=FAR)
    fixture_update(case.github, mode="close-failure")
    case.tick()
    assert len(case.closes()) == 1
    assert case.notes() == []


def test_a_lapsed_claim_of_another_machine_is_not_closed(case: Case) -> None:
    assert case.store.expire_stale(now=FAR)
    case.shell(
        "PHASE_MACHINE_ID=worker-two\n"
        '_DRIVE_RC=""; number=""; _drive_choose_selection\n'
    )
    assert case.closes() == []
