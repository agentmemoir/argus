# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A repair session that outlives its wall time is named, and never killed."""

import copy
import json
import os
import subprocess
from pathlib import Path

import argus.config.cli as cfgmod
from config_fixture import BASE, expect_error, write

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

PRELUDE = """\
set -euo pipefail
export PYTHON=python3 REPO="$FAKE/repo" RUN_DIR="$FAKE/state" LOG="$FAKE/log" DRY_RUN=0
export ARGUS="$FAKE/argus" REPAIR_PIPELINE="$FAKE/repair.yaml"
export CMD_GIT=git PIDFILE="$FAKE/pid" HALT="$FAKE/halt" DRIVER_STUB_COORDINATION=
ARGUS_CONFIG_ARGS=(--config c.jsonc)
mkdir -p "$RUN_DIR" "$REPO"
git -C "$REPO" init -q
source "$DRIVER/lib-driver-log.sh"
source "$DRIVER/lib-driver-select.sh"
source "$DRIVER/lib-driver-repair.sh"
tmux_set_title() { :; }
notify() { echo "$*" >>"$FAKE/notices"; }
"""

# A fixed clock, so two calls in one test compute the same age and the same
# line: a test that leaned on the real clock would cross a second and read one
# report as two.
CLOCK = 'date() { printf "%s\\n" 1000; }\n'


def run(tmp_path: Path, script: str, **env: str) -> int:
    base = {"FAKE": str(tmp_path), "DRIVER": str(DRIVER), "PATH": os.environ["PATH"]}
    done = subprocess.run(["bash", "-c", PRELUDE + script], env=base | env, check=False)
    return done.returncode


def read(tmp_path: Path, name: str) -> list[str]:
    path = tmp_path / name
    return path.read_text().splitlines() if path.exists() else []


def start_file(tmp_path: Path, begun: int, id: str = "7-1") -> Path:
    """Leave the record a launch writes, started at this clock second."""
    path = tmp_path / "state" / f"repair-{id}.start"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{begun}\n")
    return path


def test_a_session_younger_than_the_limit_says_nothing(tmp_path: Path) -> None:
    start_file(tmp_path, 950)
    code = run(tmp_path, CLOCK + "export REPAIR_STALE_AFTER=120\nrepair_stale")
    assert code == 1
    assert read(tmp_path, "log") == []
    assert read(tmp_path, "notices") == []


def test_a_session_past_the_limit_logs_once_and_notifies_once(tmp_path: Path) -> None:
    start_file(tmp_path, 900)
    script = CLOCK + "export REPAIR_STALE_AFTER=60\nrepair_stale; repair_stale\n"
    assert run(tmp_path, script) == 0
    log = read(tmp_path, "log")
    assert len(log) == 1
    assert "repair 7-1" in log[0] and "100s" in log[0] and "60s" in log[0]
    assert len(read(tmp_path, "notices")) == 1


def test_zero_disables_the_watch(tmp_path: Path) -> None:
    start_file(tmp_path, 1)
    script = CLOCK + "export REPAIR_STALE_AFTER=0\nrepair_stale\n"
    assert run(tmp_path, script) == 1
    assert read(tmp_path, "log") == []
    assert read(tmp_path, "notices") == []


def test_no_start_file_says_nothing(tmp_path: Path) -> None:
    code = run(tmp_path, CLOCK + "export REPAIR_STALE_AFTER=60\nrepair_stale")
    assert code == 1
    assert read(tmp_path, "log") == []
    assert read(tmp_path, "notices") == []


def test_a_record_that_is_not_a_number_says_nothing(tmp_path: Path) -> None:
    record = tmp_path / "state" / "repair-7-1.start"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("soon\n")
    code = run(tmp_path, CLOCK + "export REPAIR_STALE_AFTER=60\nrepair_stale")
    assert code == 1
    assert read(tmp_path, "log") == []


def test_a_launch_records_when_the_session_started(tmp_path: Path) -> None:
    script = (
        "tmux_session_ensure() { :; }\n"
        "tmux_phase_window_open() { :; }\n"
        "tmux_phase_window_select() { :; }\n"
        "export SELF=/bin/true SESSION=s\nrepair_launch 7-1 /t/task.md b abc123 n1\n"
    )
    assert run(tmp_path, script) == 0
    started = (tmp_path / "state" / "repair-7-1.start").read_text().strip()
    assert started.isdigit()


def test_the_session_removes_its_record_when_it_ends(tmp_path: Path) -> None:
    stub = tmp_path / "argus" / "gitsync" / "main.py"
    stub.parent.mkdir(parents=True)
    stub.write_text('print(\'{"ok": true, "action": "nothing-to-push"}\')\n')
    start_file(tmp_path, 900)
    script = "export CMD_GLIDE=true\nrun_repair_session 7-1 /t/task.md b\nexit 0\n"
    assert run(tmp_path, script) == 0
    assert not (tmp_path / "state" / "repair-7-1.start").exists()


def test_a_session_stopped_by_a_signal_removes_its_record(tmp_path: Path) -> None:
    start_file(tmp_path, 900)
    script = (
        "send_term() { kill -TERM $$; }\n"
        "export CMD_GLIDE=send_term\n"
        "run_repair_session 7-1 /t/task.md b\n"
    )
    assert run(tmp_path, script) == 130
    assert not (tmp_path / "state" / "repair-7-1.start").exists()
    assert not (tmp_path / "pid").exists()


# The key itself is optional, and a negative limit is the one value it refuses.
def test_stale_after_is_optional_and_never_negative(tmp_path: Path) -> None:
    whole = {"pipeline": "repair.yaml", "attempts": 1, "wait": 0}
    document = copy.deepcopy(BASE)
    assert "repair" not in BASE
    document["repair"] = {**whole, "stale-after": 0}
    config = cfgmod.load(str(write(tmp_path, json.dumps(document))))
    assert config.get("repair.stale-after") == 0

    document["repair"] = {**whole, "stale-after": -5}
    path = write(tmp_path, json.dumps(document))
    expect_error(lambda: cfgmod.load(str(path)), "repair.stale-after")
