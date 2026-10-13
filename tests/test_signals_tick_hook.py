# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver's pull request signal hook logs each change kind once per tick it is seen."""

import json
import os
import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

# A stand-in for `gitsync/main.py`: it counts its runs and prints the answer the
# test put in `answer.json`, or fails with no output when that file is absent.
FAKE_MAIN = """\
import pathlib, sys
here = pathlib.Path(sys.argv[0]).resolve().parents[2]
with (here / "runs").open("a") as runs:
    runs.write(" ".join(sys.argv[1:]) + "\\n")
answer = here / "answer.json"
if not answer.exists():
    sys.exit(3)
sys.stdout.write(answer.read_text())
"""

DRIVE = """\
set -euo pipefail
export PYTHON=python3 ARGUS="$FAKE/argus" REPO=/repo RUN_DIR="$FAKE/state" LOG="$FAKE/log"
export DRY_RUN=0 DRIVER_STUB_COORDINATION=
ARGUS_CONFIG_ARGS=(--config c.jsonc)
source "$DRIVER/lib-driver-log.sh"
source "$DRIVER/lib-driver-select.sh"
source "$DRIVER/lib-driver-group.sh"
for phase in "$@"; do
  watch_signals_pull_request "$phase"
done
"""


def answer(state: str, events: list[dict[str, str]]) -> str:
    return json.dumps({"ok": True, "state": state, "reasons": [], "events": events})


def run_ticks(tmp_path: Path, *phases: str) -> list[str]:
    """Run one signal hook call per phase; return the log lines."""
    main = tmp_path / "argus" / "gitsync" / "main.py"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text(FAKE_MAIN)
    (tmp_path / "state").mkdir(exist_ok=True)
    env = {"FAKE": str(tmp_path), "DRIVER": str(DRIVER), "PATH": os.environ["PATH"]}
    subprocess.run(["bash", "-c", DRIVE, "bash", *phases], env=env, check=True)
    log = tmp_path / "log"
    return log.read_text().splitlines() if log.exists() else []


def test_new_changes_are_logged_by_kind(tmp_path: Path) -> None:
    events = [{"kind": "alert_opened"}, {"kind": "comment_added"}]
    (tmp_path / "answer.json").write_text(answer("WATCHED", events))
    lines = run_ticks(tmp_path, "100030")
    assert len(lines) == 1
    assert lines[0].endswith(
        "pull request signals for phase 100030: 1 alert_opened, 1 comment_added"
    )


def test_a_tick_with_no_new_change_logs_nothing(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text(answer("WATCHED", []))
    assert run_ticks(tmp_path, "100030") == []


def test_a_phase_with_no_pull_request_logs_nothing(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text(answer("NO-PULL-REQUEST", []))
    assert run_ticks(tmp_path, "100030") == []


def test_an_unreadable_snapshot_is_logged_once_as_not_read(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text(answer("UNAVAILABLE", []))
    lines = run_ticks(tmp_path, "100030", "100030")
    assert len(lines) == 1
    assert "signals-100030.json" in lines[0]
    assert ".err" not in lines[0]
