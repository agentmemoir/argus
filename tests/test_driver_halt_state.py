# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A halt names the state and reason glide left in the session log."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def load_helper():
    """The helper module, so a test can call its read path directly."""
    spec = importlib.util.spec_from_file_location(
        "session_state", DRIVER / "session_state.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-launchfault; do
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


def run_with_payload(tmp_path: Path, payload: str | None, setup: str = "") -> str:
    """Run the halt branch, then let it read what this launch appended.

    The driver records the log's length at start, so the append is ordered
    after that point rather than timed: the stub waits for a file, and the
    file is created only after the append. `payload` of None models the path
    where glide never starts and appends nothing.
    """
    append = (
        ""
        if payload is None
        else "cat >>\"$RUN_DIR/session-100010.log\" <<'PAYLOAD'\n"
        + payload
        + "PAYLOAD\n"
    )
    body = (
        setup
        + 'DRIVER_STUB_WAIT_FILE="$RUN_DIR/go"\n'
        + "( run_session 100010 phase.md ) &\n"
        + "pid=$!\n"
        + 'while [ ! -f "$PIDFILE" ]; do sleep 0.01; done\n'
        + append
        + ': >"$RUN_DIR/go"\n'
        + 'wait "$pid" || true\n'
    )
    return shell(tmp_path, body)


def test_session_log_payload_names_the_halt(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    run_with_payload(tmp_path, SESSION_LOG)
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    record = halt_state(tmp_path)
    assert "state=config_error" in marker
    assert "state=config_error source=session-log" in events
    assert record["state"] == "config_error"
    reason = record["reason"]
    assert isinstance(reason, str)
    assert "hook pre_run failed (exit 1)" in reason
    assert "missing {repo} key" in reason
    assert "\n" not in reason


def test_missing_payload_keeps_unknown(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    run_with_payload(tmp_path, "plan: fixture stage\nno result here\n")
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    assert "state=unknown" in marker
    assert halt_state(tmp_path)["state"] == ""


def test_payload_from_an_earlier_launch_keeps_unknown(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(SESSION_LOG, encoding="utf-8")
    run_with_payload(tmp_path, None)
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    assert "state=unknown" in marker
    assert "state=unknown source=rc" in events
    assert halt_state(tmp_path)["state"] == ""


def test_failed_size_read_does_not_adopt_an_earlier_payload(
    tmp_path: Path, exec_dir: Path
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(SESSION_LOG, encoding="utf-8")
    # The size read runs through PYTHON, so a wrapper that fails only that -c
    # script models a stat that cannot be read while the helper still works.
    wrapper = exec_dir / "fake-python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-c" ]; then\n'
        '  case "$2" in\n'
        "    *getsize*) exit 1 ;;\n"
        "  esac\n"
        "fi\n"
        'exec "$TEST_PYTHON" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    run_with_payload(tmp_path, None, setup=f'PYTHON="{wrapper}"\n')
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    record = halt_state(tmp_path)
    assert "state=unknown" in marker
    assert "state=unknown source=rc" in events
    assert record["state"] == ""
    assert record["source"] == "rc"
    gate = shell(
        tmp_path,
        'rc=0; reason="$(resume_gate_reason "" "$RUN_DIR")" || rc=$?\n'
        'printf "%s\\nrc=%s\\n" "$reason" "$rc"\n',
    )
    assert "no resume.json" in gate
    assert gate.rstrip().endswith("rc=1")


def test_damaged_run_json_is_not_read_from_the_session_log(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/run.json").write_text('{"state": ', encoding="utf-8")
    run_with_payload(
        tmp_path, '{"state": "blocked", "error": "recovered from the log"}\n'
    )
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    events = (tmp_path / "run/events").read_text(encoding="utf-8")
    record = halt_state(tmp_path)
    assert "state=unknown" in marker
    assert "state=unknown source=rc" in events
    assert record["state"] == ""
    gate = shell(
        tmp_path,
        'rc=0; reason="$(resume_gate_reason "" "$RUN_DIR")" || rc=$?\n'
        'printf "%s\\nrc=%s\\n" "$reason" "$rc"\n',
    )
    assert "unreadable or damaged" in gate
    assert gate.rstrip().endswith("rc=1")


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
            [sys.executable, str(helper), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        assert result.returncode != 0, name
        assert result.stdout == "", name
    absent = subprocess.run(
        [sys.executable, str(helper), str(tmp_path / "absent.log")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert absent.returncode != 0
    assert absent.stdout == ""


def test_one_invocation_yields_state_error_and_hook(tmp_path: Path) -> None:
    path = tmp_path / "session.log"
    path.write_text(SESSION_LOG, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(DRIVER / "session_state.py"), str(path), "0"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "state=config_error"
    assert lines[1].startswith("error=hook pre_run failed (exit 1):")
    assert lines[2] == "hook=pre_run"
    assert len(lines) == 3


def test_reads_no_more_than_the_window(tmp_path: Path) -> None:
    module = load_helper()
    path = tmp_path / "big.log"
    # A log far larger than the window, whose payload is the real last object.
    with path.open("wb") as handle:
        handle.write(b"plan: filler stage\n")
        handle.write(b"x" * (4 * module.WINDOW_BYTES))
        handle.write(b"\n" + SESSION_LOG.encode("utf-8"))
    size = path.stat().st_size
    read = module.read_window(path, 0)
    assert len(read) <= module.WINDOW_BYTES
    assert len(read) < size
    # The offset case reads from the offset, not the whole file.
    offset = size - len(SESSION_LOG.encode("utf-8"))
    assert len(module.read_window(path, offset)) <= module.WINDOW_BYTES
    assert module.trailing_object(read.decode("utf-8")) == {
        "state": "config_error",
        "error": "hook pre_run failed (exit 1):\nmissing {repo} key at line 0 of 1",
        "hook_point": "pre_run",
        "hook_exit": 1,
    }


def test_valid_run_json_is_not_overridden_by_the_session_log(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/session-100010.log").write_text(SESSION_LOG, encoding="utf-8")
    (tmp_path / "run/run.json").write_text('{"state": "blocked"}', encoding="utf-8")
    shell(tmp_path, "( run_session 100010 phase.md ) || true\n")
    marker = (tmp_path / "run/halted").read_text(encoding="utf-8")
    assert "state=blocked" in marker
    assert halt_state(tmp_path)["state"] == "blocked"
