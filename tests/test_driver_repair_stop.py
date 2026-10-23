# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A failed repair read ends the tick before selection; a busy window still reads CI."""

from pathlib import Path

from driver_maintenance_support import shell

SETUP = """
REPAIR_PIPELINE="$TEST_ROOT/repair.yaml"; : >"$REPAIR_PIPELINE"
REPAIR_ATTEMPTS=3; REPAIR_WAIT=600
eval "$(declare -f python_transport | sed '1s/^python_transport/orig_python_transport/')"
python_transport() {
  case "$1" in
    */gitsync/repair_cli.py) log "CLI"; printf '%s' "$DECISION"; return "${RC:-0}" ;;
    *) orig_python_transport "$@" ;;
  esac
}
repair_launch() { log "REPAIR_LAUNCH $*"; }
notify() { log "NOTIFY $*"; }
check_stall() { :; }
"""


def _events(tmp_path: Path, body: str) -> list[str]:
    shell(tmp_path, SETUP + body)
    return (tmp_path / "events").read_text(encoding="utf-8").splitlines()


def test_a_failed_checks_read_ends_the_tick_before_selection(tmp_path: Path) -> None:
    events = _events(
        tmp_path, 'DECISION=\'{"action":"unread","read":"checks"}\'; RC=3\n_drive\n'
    )
    assert "SELECT" not in events
    assert any(
        e.startswith("repair: the checks of")
        and "could not be read (rc=3); no new work this tick" in e
        for e in events
    )


def test_a_failed_head_read_names_the_head_and_ends_the_tick(tmp_path: Path) -> None:
    events = _events(
        tmp_path, 'DECISION=\'{"action":"unread","read":"head"}\'; RC=3\n_drive\n'
    )
    assert "SELECT" not in events
    assert any(e.startswith("repair: the head of") for e in events)


def test_a_cleared_notice_is_logged_and_new_work_still_selects(tmp_path: Path) -> None:
    cleared = (
        "Argus cleared the pending-check notice for "
        "https://example.test/pull/1: no check is pending."
    )
    events = _events(
        tmp_path,
        f'DECISION=\'{{"action":"none","cleared":"{cleared}"}}\'\n_drive\n',
    )
    assert cleared in events
    assert "SELECT" in events
    assert not any("could not be read" in e for e in events)


def test_a_pending_notice_reaches_notify_and_new_work_goes_on(tmp_path: Path) -> None:
    events = _events(
        tmp_path,
        'DECISION=\'{"action":"none","notice":"Needs You. x"}\'\n_drive\n',
    )
    assert "NOTIFY Needs You. x" in events
    assert "SELECT" in events


def test_a_busy_window_still_reads_checks_and_starts_no_round(tmp_path: Path) -> None:
    events = _events(
        tmp_path,
        "tmux_busy() { return 0; }\n"
        'DECISION=\'{"action":"repair","task":"/t.md"}\'\n_drive\n',
    )
    assert "CLI" in events
    assert "repair 1 waits: the phase window is running" in events
    assert not any(e.startswith("REPAIR_LAUNCH") for e in events)
    assert "SELECT" not in events


def test_a_busy_issue_window_reads_issue_checks_and_starts_no_round(
    tmp_path: Path,
) -> None:
    snapshot = (
        "issue_review_snapshot() { printf '%s' "
        '\'{"awaiting_review":[{"issue":9,"branch":"issue/9-docs",'
        '"pull_request":"https://example.test/pull/9"}]}\'; }\n'
    )
    events = _events(
        tmp_path,
        "tmux_busy() { return 0; }\nWORK_SOURCE=issues\n"
        + snapshot
        + 'DECISION=\'{"action":"repair","task":"/t.md"}\'\n_drive\n',
    )
    assert "CLI" in events
    assert not any(e.startswith("REPAIR_LAUNCH") for e in events)
    assert "SELECT" not in events


def test_an_unlisted_issue_pull_request_ends_the_tick(tmp_path: Path) -> None:
    events = _events(
        tmp_path, "WORK_SOURCE=issues\nissue_review_snapshot() { return 3; }\n_drive\n"
    )
    assert "SELECT" not in events
    assert any("issue pull requests could not be listed" in e for e in events)
