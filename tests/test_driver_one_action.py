# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One action per issue tick: a readied or still-waiting pull request ends the tick."""

from pathlib import Path

from driver_maintenance_support import shell

ISSUE_PR = "https://example.test/pull/9"
READY_SWEEP = (
    '{"state":"READY","results":[{"kind":"issue","state":"READY",'
    f'"pull_request":"{ISSUE_PR}","skipped":false}}]}}'
)
OPEN_READY = (
    '{"awaiting_review":[{"issue":9,"branch":"issue/9-docs",'
    f'"pull_request":"{ISSUE_PR}","draft":false}}]}}'
)
OPEN_DRAFT = OPEN_READY.replace('"draft":false', '"draft":true')
NONE_OPEN = '{"awaiting_review":[]}'

SETUP = """
REPAIR_PIPELINE="$TEST_ROOT/repair.yaml"; : >"$REPAIR_PIPELINE"
REPAIR_ATTEMPTS=3; REPAIR_WAIT=600; WORK_SOURCE=issues
READY_JSON='{"state":"IDLE","results":[]}'; DECISION='{"action":"none"}'
eval "$(declare -f python_transport | sed '1s/^python_transport/orig_python_transport/')"
python_transport() {
  case "$1" in
    */gitsync/main.py)
      case "$*" in
        *"--mode ready"*) printf '%s' "$READY_JSON"; return 0 ;;
      esac
      orig_python_transport "$@" ;;
    */gitsync/repair_cli.py) log "CLI"; printf '%s' "$DECISION"; return 0 ;;
    *) orig_python_transport "$@" ;;
  esac
}
repair_remote_head() { printf 'abc123'; }
repair_launch() { log "REPAIR_LAUNCH $*"; }
notify() { log "NOTIFY $*"; }
"""


def _events(tmp_path: Path, body: str) -> list[str]:
    shell(tmp_path, SETUP + body)
    return (tmp_path / "events").read_text(encoding="utf-8").splitlines()


def _listing(rows: str) -> str:
    return f"issue_review_snapshot() {{ printf '%s' '{rows}'; }}\n"


def test_a_readied_issue_pull_request_ends_the_tick_before_selection(
    tmp_path: Path,
) -> None:
    events = _events(
        tmp_path,
        f"READY_JSON='{READY_SWEEP}'\n" + _listing(OPEN_READY) + "_drive\n",
    )
    assert "SELECT" not in events
    assert "ready: PR #9 marked ready; no new work this tick" in events


def test_a_draft_with_pending_checks_ends_the_tick_before_selection(
    tmp_path: Path,
) -> None:
    events = _events(
        tmp_path,
        _listing(OPEN_DRAFT)
        + 'DECISION=\'{"action":"none","waiting":"in draft, checks pending"}\'\n_drive\n',
    )
    assert "SELECT" not in events
    assert "waiting: PR #9 in draft, checks pending; no new work this tick" in events


def test_an_escalated_pull_request_does_not_block_selection(tmp_path: Path) -> None:
    events = _events(
        tmp_path,
        _listing(OPEN_DRAFT)
        + 'DECISION=\'{"action":"escalate","message":"Needs You. x"}\'\n_drive\n',
    )
    assert "SELECT" in events
    assert not any(e.startswith("waiting:") for e in events)


def test_no_open_pull_request_starts_selection(tmp_path: Path) -> None:
    events = _events(tmp_path, _listing(NONE_OPEN) + "_drive\n")
    assert "CLI" not in events
    assert "SELECT" in events
