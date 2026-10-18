# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A halt names the state and reason glide left in the session log."""

import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

#: A session log as glide writes it: the plan text, then one JSON object.
SESSION_LOG = (
    "plan: fixture stage\n"
    '{"state": "config_error", "error": "hook pre_run failed (exit 1):\\n'
    'missing {repo} key at line 0 of 1", "hook_point": "pre_run", '
    '"hook_exit": 1}\n'
)


def shell(tmp_path: Path, body: str) -> str:
    script = """
set -euo pipefail
for name in lib-driver-log lib-driver-run lib-driver-halt-record; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_DRIVER/.."; REPO="$TEST_ROOT"
RUN_DIR="$TEST_ROOT/run"; mkdir -p "$RUN_DIR"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"
CLAIM="$RUN_DIR/claim"; CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
DRY_RUN=0; work_kind=phase
ARGUS_CONFIG_ARGS=()
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
notify_phase() { :; }
issue_announce() { :; }
tmux_set_title() { :; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
DRIVER_STUB_RC=2; DRIVER_STUB_SLEEP=0
"""
    result = subprocess.run(
        ["bash", "-c", script + body],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
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


def halt_state(tmp_path: Path) -> dict[str, object]:
    return json.loads((tmp_path / "run/halted.json").read_text(encoding="utf-8"))


def test_session_log_payload_names_the_halt(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(SESSION_LOG, encoding="utf-8")
    shell(tmp_path, "( run_session 100010 phase.md ) || true\n")
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    record = halt_state(tmp_path)
    assert "state=config_error" in marker
    assert "state=config_error source=rc" in events
    assert record["state"] == "config_error"
    reason = record["reason"]
    assert isinstance(reason, str)
    assert "hook pre_run failed (exit 1)" in reason
    assert "missing {repo} key" in reason
    assert "\n" not in reason


def test_missing_payload_keeps_unknown(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(
        "plan: fixture stage\nno result here\n", encoding="utf-8"
    )
    shell(tmp_path, "( run_session 100010 phase.md ) || true\n")
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    assert "state=unknown" in marker
    assert halt_state(tmp_path)["state"] == ""


def test_helper_prints_nothing_it_cannot_read(tmp_path: Path) -> None:
    helper = DRIVER / "session_state.py"
    assert helper.is_file()
    cases = {
        "truncated": 'plan\n{"state": "config_error", "error": "cut',
        "no_state": 'plan\n{"error": "no state here"}',
        "not_a_string": 'plan\n{"state": 3}',
    }
    for name, text in cases.items():
        path = tmp_path / f"{name}.log"
        path.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(helper), str(path), "state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        assert result.returncode != 0, name
        assert result.stdout == "", name
    absent = subprocess.run(
        [sys.executable, str(helper), str(tmp_path / "absent.log"), "state"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert absent.returncode != 0
    assert absent.stdout == ""


def test_valid_run_json_is_not_overridden_by_the_session_log(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(SESSION_LOG, encoding="utf-8")
    (tmp_path / "run/run.json").write_text('{"state": "blocked"}', encoding="utf-8")
    shell(tmp_path, "( run_session 100010 phase.md ) || true\n")
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    assert "state=blocked" in marker
    assert halt_state(tmp_path)["state"] == "blocked"
