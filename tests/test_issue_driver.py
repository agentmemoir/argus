# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue results follow the tick without entering the phase review path."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.notify.notifications import format_notification

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def shell(
    tmp_path: Path,
    body: str,
    selection: dict[str, object],
    extra_env: dict[str, str] | None = None,
) -> str:
    fixture = tmp_path / "selection.json"
    fixture.write_text(json.dumps(selection), encoding="utf-8")
    script = "\n".join(
        f'. "$TEST_DRIVER/{name}"'
        for name in (
            "lib-driver-log.sh",
            "lib-driver-select.sh",
            "lib-driver-issues.sh",
            "lib-driver-config.sh",
            "lib-driver-group.sh",
            "lib-driver-select-flow.sh",
            "lib-driver-select-real.sh",
            "lib-driver-maintenance.sh",
            "lib-driver-dryrun.sh",
        )
    )
    script += """
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."
REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT"
LOG="$TEST_ROOT/driver.log"
ARGUS_CONFIG_ARGS=(--config fixture.json)
BOARD_KIND_ARGS=()
DRY_RUN=0
WORK_SOURCE=issues
PHASE_MACHINE_ID=fixture-machine
RECOVER_OWN_CLAIM=0
DRIVER_STUB_COORDINATION=fixture
_DRIVE_RC=""
out=""; rc=0; selection_rc=0
SESSION=fixture
notify() { :; }
notify_coordination_outage() { :; }
_drive_stub_selection() {
  selection="$(cat "$TEST_ROOT/selection.json")"
  selection_state="$(printf '%s' "$selection" | selection_value state)"
}
"""
    result = subprocess.run(
        ["bash", "-u", "-c", script + body],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            **(extra_env or {}),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("kind", ["issue", "phase", None])
@pytest.mark.parametrize("state", ["RESERVED", "WAIT_FOR_CLAIM", "COOLDOWN"])
def test_tick_binds_key_in_each_state(
    tmp_path: Path, kind: str | None, state: str
) -> None:
    selection: dict[str, object] = {
        "state": state,
        "issue": "23",
        "phase": "000007",
        "path": "tasks/issue-000023-fixture.md",
        "owner": "other-machine",
        "status": "claimed",
        "reservation_id": "fixture-reservation",
        "generation": 1,
        "skipped": {},
    }
    if kind:
        selection["kind"] = kind
    output = shell(
        tmp_path,
        '_drive_choose_selection\nprintf "%s\\t%s\\t%s" "$work_kind" "$item_key" "${number:-}"',
        selection,
    )
    fields = output.split("\t")
    expected_kind = kind or "phase"
    assert fields[:2] == [expected_kind, expected_kind]
    if state == "RESERVED":
        assert fields[2] == ("23" if kind == "issue" else "000007")
    if state == "WAIT_FOR_CLAIM":
        log = (tmp_path / "driver.log").read_text(encoding="utf-8")
        assert f"for {expected_kind} " in log


def test_pending_report_cannot_relaunch(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        'RECOVER_OWN_CLAIM=1\n_drive_choose_selection\nprintf "%s" "$_DRIVE_RC"',
        {
            "kind": "issue",
            "state": "WAIT_FOR_CLAIM",
            "issue": "23",
            "owner": "fixture-machine",
            "status": "writeback_pending",
        },
    )
    assert output == "0"
    assert "claim kept, no launch" in (tmp_path / "driver.log").read_text()


def test_issue_does_not_take_group(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "_drive_choose_selection\ngroup_active && printf active || printf single",
        {
            "state": "RESERVED",
            "kind": "issue",
            "issue": "23",
            "path": "tasks/issue-000023-fixture.md",
            "group": ["23", "24"],
        },
    )
    assert output == "single"


def test_issue_watching_cannot_call_phase_publisher(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        """
DRIVER_STUB_COORDINATION=""
tidy_published_checkout() { return 0; }
confirm_pending_reviews() { printf forbidden-review; return 1; }
coordination_select() { cat "$TEST_ROOT/selection.json"; }
sync_branch_args() { SYNC_BRANCH_ARGS=(); }
python_standin() {
  case "$1" in */gitsync/main.py) return 0;; esac
  "$TEST_PYTHON" "$@"
}
_drive_real_selection
printf '%s' "$PENDING_PUBLISHED"
""".replace(
            "_drive_real_selection\n",
            "PYTHON=python_standin\nreconcile_pending_reviews\n_drive_real_selection\n",
        ),
        {"kind": "issue", "state": "NO_PHASE", "watching": [{"issue": "23"}]},
    )
    assert "forbidden" not in output
    assert output.endswith("0")


@pytest.mark.parametrize("event", ["started", "released", "stalled", "stopped"])
def test_notification_names_issue(event: str, tmp_path: Path) -> None:
    message = format_notification(
        "issue-000023-fix-api.md",
        event,
        kind="issue",
        run_json=tmp_path / "run.json",
    )
    assert message.startswith("Issue 000023 (Fix API)")
    assert "Phase" not in message


@pytest.mark.parametrize("fault", ["gh", "board", "folder", "owners"])
def test_issue_preflight_refuses_before_selection(tmp_path: Path, fault: str) -> None:
    body = """
CMD_GH="$TEST_PYTHON"
BOARD_REPO="$TEST_ROOT"; BOARD_FILE=state.json; BOARD_ISSUES_FILE=issues.json
ISSUES_TASK_DIR="$TEST_ROOT"
ISSUES_CODEOWNERS_FILE=""
"""
    body += {
        "gh": 'CMD_GH="$TEST_ROOT/missing-gh"\n',
        "board": 'BOARD_FILE=""\n',
        "folder": 'ISSUES_TASK_DIR="$TEST_ROOT/missing-folder"\n',
        "owners": 'ISSUES_CODEOWNERS_FILE="$TEST_ROOT/missing-owners"\n',
    }[fault]
    output = shell(
        tmp_path, body + 'issue_preflight || printf "%s" "$PREFLIGHT_REASON"', {}
    )
    expected = {
        "gh": "GitHub command",
        "board": "board.file",
        "folder": "issues.task-dir",
        "owners": "issues.codeowners-file",
    }[fault]
    assert expected in output


def test_dry_run_names_an_available_issue(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        """
HALT="$TEST_ROOT/halted"
HALT_RECORD="$TEST_ROOT/halted.json"
tmux_phase_window_alive() { return 1; }
work_preflight() { return 0; }
coordination_select() { cat "$TEST_ROOT/selection.json"; }
report_needs_decision() { :; }
SELF=argus
_drive_dry_run
""",
        {
            "state": "AVAILABLE",
            "kind": "issue",
            "issue": "23",
            "phase": "000007",
            "path": "tasks/issue-000023-fixture.md",
            "skipped": {},
        },
    )
    log = (tmp_path / "driver.log").read_text(encoding="utf-8")
    assert "would reserve and launch issue 23 in tmux" in log + output
    assert "--session 23 tasks/issue-000023-fixture.md" in output


def test_selection_log_names_only_nonzero_skip_reasons(tmp_path: Path) -> None:
    shell(
        tmp_path,
        'work_kind=issue\nreport_issue_selection "$(cat "$TEST_ROOT/selection.json")"',
        {"state": "NO_PHASE", "kind": "issue", "skipped": {"blocked": 0, "claimed": 2}},
    )
    log = (tmp_path / "driver.log").read_text(encoding="utf-8")
    assert [line for line in log.splitlines() if "issues skipped:" in line] == [
        line for line in log.splitlines() if line.endswith("issues skipped: claimed=2")
    ]
    assert "issues skipped: claimed=2" in log


@pytest.mark.parametrize("event", ["failed", "escalated"])
def test_issue_failure_notice_names_label_and_issue(event: str, tmp_path: Path) -> None:
    message = format_notification(
        "issue-000023-fix-api.md",
        event,
        kind="issue",
        run_json=tmp_path / "run.json",
        claim_label="accepted",
    )
    assert "Remove the `accepted` label from issue #23 to let Argus retry." in message
    assert "phase" not in message


@pytest.mark.parametrize("kind", ["issue", "phase"])
def test_failed_issue_run_returns_its_claim(kind: str, tmp_path: Path) -> None:
    driver = Path(__file__).resolve().parents[1] / "src/argus/driver"
    script = f"""
. "{driver}/lib-driver-fence.sh"
. "{driver}/lib-driver-launchfault.sh"
. "{driver}/lib-driver-run.sh"
log() {{ printf '%s\\n' "$*" >>"{tmp_path}/log"; }}
fake_python() {{ printf '%s ' "$@" >"{tmp_path}/call"; }}
PYTHON=fake_python ARGUS=/argus REPO=/repo RUN_DIR="{tmp_path}" CLAIM="{tmp_path}/claim"
PHASE_MACHINE_ID=m1 CLIO_RESERVATION_ID=r1 CLIO_RESERVATION_GENERATION=4
ARGUS_CONFIG_ARGS=() BOARD_KIND_ARGS=(--issues) work_kind={kind}
release_failed_issue 23
"""
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    call = tmp_path / "call"
    if kind == "phase":
        assert not call.exists()
        return
    assert call.read_text().split() == [
        "/argus/board/reservations.py",
        "release-group",
        "--issues",
        "--repo-root",
        "/repo",
        "--machine-id",
        "m1",
        "--reservation-id",
        "r1",
        "--generation",
        "4",
    ]
    assert "returned the failed issue 23" in (tmp_path / "log").read_text()
