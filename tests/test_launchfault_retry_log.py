# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The launch-fault log names the earliest retry time and the tick (#291).

A retry runs only on a driver tick, so the log gives the stored due time as a
clock time and says that the retry runs on the next tick at or after it.
"""

import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
NOW = 1_800_000_000

HALT = """set -euo pipefail
. "$1/lib-driver-launchfault.sh"
RUN_DIR="$2" HALT="$2/halted" TEST_NOW="$3" work_kind=issue
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
halt_state_from_session() { HALT_LOG_STATE="" HALT_LOG_REASON=""; }
halt_record_write() { :; }
issue_stop_save() { return 0; }
notify() { :; }
tmux_set_title() { :; }
launchfault_now() { printf %s "$TEST_NOW"; }
launchfault_halt 7 tasks/issue.md "$RUN_DIR" config_error 2 "" ""
launchfault_halt 7 tasks/issue.md "$RUN_DIR" config_error 2 "" ""
"""


def clock(epoch: int) -> str:
    out = subprocess.run(
        [
            "sh",
            "-c",
            'date -d "@$1" +%H:%M 2>/dev/null || date -r "$1" +%H:%M',
            "-",
            str(epoch),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_log_names_the_due_time_and_the_next_tick(tmp_path: Path) -> None:
    subprocess.run(
        ["bash", "-c", HALT, "-", str(DRIVER), str(tmp_path), str(NOW)],
        check=True,
    )
    lines = (tmp_path / "events").read_text(encoding="utf-8").splitlines()
    stored = int((tmp_path / "launch-fault").read_text(encoding="utf-8").split()[2])
    assert stored == NOW + 1200
    for line, due in zip(lines, (NOW + 600, NOW + 1200), strict=True):
        assert f"retry on the first tick at or after {clock(due)}" in line, line
        assert "retry in" not in line, line
