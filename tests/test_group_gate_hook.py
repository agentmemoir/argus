# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver's group gate hook runs once per pull request and logs a changed answer once."""

import json
import os
import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
URL = "https://github.com/example-org/example-repo/pull/7"

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
for tick in "$@"; do
  GATED_PULL_REQUESTS=""
  for entry in $tick; do
    gate_group_pull_request "${entry%%=*}" "${entry#*=}"
  done
done
"""


def answer(state: str, summary: str) -> str:
    return json.dumps({"ok": True, "state": state, "summary": summary})


def run_ticks(tmp_path: Path, *ticks: str) -> tuple[list[str], int]:
    """Run one gate hook call per entry of each tick; return the log and the runs."""
    main = tmp_path / "argus" / "gitsync" / "main.py"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text(FAKE_MAIN)
    (tmp_path / "state").mkdir(exist_ok=True)
    env = {"FAKE": str(tmp_path), "DRIVER": str(DRIVER), "PATH": os.environ["PATH"]}
    subprocess.run(["bash", "-c", DRIVE, "bash", *ticks], env=env, check=True)
    log = tmp_path / "log"
    runs = tmp_path / "runs"
    lines = log.read_text().splitlines() if log.exists() else []
    return lines, len(runs.read_text().splitlines()) if runs.exists() else 0


def test_an_unchanged_answer_is_logged_once(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text(answer("HELD", "group gate HELD (running)"))
    entry = f"100030={URL}"
    lines, runs = run_ticks(tmp_path, entry, entry, entry)
    assert runs == 3
    assert len(lines) == 1
    assert lines[0].endswith("group gate for phase 100030: group gate HELD (running)")


def test_a_changed_answer_is_logged_again(tmp_path: Path) -> None:
    entry = f"100030={URL}"
    (tmp_path / "answer.json").write_text(answer("HELD", "group gate HELD (running)"))
    run_ticks(tmp_path, entry)
    (tmp_path / "answer.json").write_text(answer("READY", "group gate READY"))
    lines, _ = run_ticks(tmp_path, entry)
    assert [line.split(": ", 1)[1] for line in lines] == [
        "group gate HELD (running)",
        "group gate READY",
    ]


def test_members_sharing_a_pull_request_run_the_gate_once_per_tick(
    tmp_path: Path,
) -> None:
    (tmp_path / "answer.json").write_text(answer("HELD", "group gate HELD (running)"))
    _, runs = run_ticks(tmp_path, f"100030={URL} 100029={URL} 100028={URL}")
    assert runs == 1


def test_a_phase_outside_a_group_logs_nothing(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text(answer("NO-GROUP", "group gate NO-GROUP"))
    lines, runs = run_ticks(tmp_path, f"100040={URL}")
    assert runs == 1
    assert lines == []


def test_a_gate_that_gives_no_answer_is_logged_once_with_its_error_file(
    tmp_path: Path,
) -> None:
    entry = f"100030={URL}"
    lines, runs = run_ticks(tmp_path, entry, entry)
    assert runs == 2
    assert len(lines) == 1
    assert "no answer (see " in lines[0]
    assert lines[0].rstrip().endswith("group-gate-100030.json.err)")
