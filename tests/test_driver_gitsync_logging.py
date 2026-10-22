# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The clean paths say what their result file reported, and never halt the line.

Both clean calls answer ok and exit 0 for work they did not do: a phase commit
reports a repair in warning, and the whole-tree sweep names an unproven fence in
skipped while a sibling phase lands. Neither fact reaches the log through the
exit code, so the driver reads the file it just wrote.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

FAKE_MAIN = """import os, sys
root = os.environ["TEST_ROOT"]
count_file = os.path.join(root, "count")
count = int(open(count_file).read()) if os.path.exists(count_file) else 0
open(count_file, "w").write(str(count + 1))
name = "payload.json"
if count > 0 and os.path.exists(os.path.join(root, "payload2.json")):
    name = "payload2.json"
sys.stdout.write(open(os.path.join(root, name)).read())
sys.exit(int(os.environ.get("TEST_EXIT", "0")))
"""

SCRIPT = """
set -euo pipefail
for name in lib-driver-log lib-driver-run; do
  . "$TEST_DRIVER/$name.sh"
done
ts() { printf fixture-time; }
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_ROOT/argus"; REPO="$TEST_ROOT/root"
RUN_DIR="$TEST_ROOT/root/run"; LOG="$RUN_DIR/events"
DRY_RUN=0; work_kind=phase
ARGUS_CONFIG_ARGS=(--config fixture.json); GITSYNC_KIND_ARGS=()
rc=0
case "$TEST_CALL" in
  clean) gitsync_clean 100020 || rc=$? ;;
  repeat)
    gitsync_land_leftovers || rc=$?
    gitsync_land_leftovers || rc=$? ;;
  nonregular) gitsync_log_result "$RUN_DIR" "leftover run state" || rc=$? ;;
  missing) gitsync_log_result "$RUN_DIR/absent.json" "leftover run state" || rc=$? ;;
  *) gitsync_land_leftovers || rc=$? ;;
esac
printf 'rc=%s\\n' "$rc"
"""


def run_clean(
    tmp_path: Path,
    payload: str,
    call: str = "land",
    exit_code: int = 0,
    payload2: str | None = None,
) -> tuple[str, list[str]]:
    """Run one clean function over a fake main.py printing payload."""
    root = tmp_path / "root"
    (root / "run").mkdir(parents=True)
    (tmp_path / "argus" / "gitsync").mkdir(parents=True)
    (tmp_path / "argus" / "gitsync" / "main.py").write_text(FAKE_MAIN)
    (tmp_path / "payload.json").write_text(payload)
    if payload2 is not None:
        (tmp_path / "payload2.json").write_text(payload2)
    completed = subprocess.run(
        ["bash", "-c", SCRIPT],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_CALL": call,
            "TEST_EXIT": str(exit_code),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    events = root / "run" / "events"
    lines = events.read_text().splitlines() if events.exists() else []
    return completed.stdout, lines


def test_two_skipped_entries_each_get_one_log_line(tmp_path: Path) -> None:
    payload = (
        json.dumps(
            {
                "ok": True,
                "mode": "clean",
                "state": "PARTIAL",
                "repos": [],
                "pathspec": "runs",
                "skipped": [
                    {
                        "phase": "100020",
                        "state": "FENCED",
                        "error": "phase 100020 is held by a live claim of another machine",
                        "detail": "skipped: the FENCED fence refused phase 100020, so its "
                        "leftover run state was not landed",
                    },
                    {
                        "phase": "100021",
                        "state": "COORDINATION_UNAVAILABLE",
                        "error": "cannot read coordination state",
                        "detail": "skipped: the COORDINATION_UNAVAILABLE fence refused "
                        "phase 100021, so its leftover run state was not landed",
                    },
                ],
            }
        )
        + "\n"
    )
    out, lines = run_clean(tmp_path, payload)
    assert "rc=0" in out
    skipped = [line for line in lines if "skipped phase" in line]
    assert len(skipped) == 2, lines
    assert "100020" in skipped[0]
    assert "held by a live claim of another machine" in skipped[0]
    assert "FENCED" in skipped[0]
    assert "100021" in skipped[1]
    assert "cannot read coordination state" in skipped[1]
    assert skipped[0] == (
        "fixture-time leftover run state skipped phase 100020 (FENCED): "
        "phase 100020 is held by a live claim of another machine"
    )
    # `detail` repeats the phase and the state, so it is not on the line.
    assert not any("was not landed" in line for line in lines), lines


@pytest.mark.parametrize("call", ["clean", "land"])
def test_a_warning_on_an_ok_result_logs_one_degraded_line(
    tmp_path: Path, call: str
) -> None:
    payload = (
        json.dumps(
            {
                "ok": True,
                "action": "continued",
                "repo": "/nonexistent/clone",
                "pathspec": "runs/phase-100020",
                "warning": "commit of run state failed: git exited 128; the next tick retries",
            }
        )
        + "\n"
    )
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    assert len(lines) == 1, lines
    assert "continued in a degraded state" in lines[0]
    assert "commit of run state failed" in lines[0]
    label = (
        "phase 100020 closing run state" if call == "clean" else "leftover run state"
    )
    assert lines[0] == (
        f"fixture-time {label} continued in a degraded state: commit of run state "
        "failed: git exited 128; the next tick retries"
    )


@pytest.mark.parametrize("call", ["clean", "land"])
@pytest.mark.parametrize("payload", ["", "{not json\n", "[1, 2, 3]\n"])
def test_the_reader_contract_stays_silent_on_an_empty_or_malformed_file(
    tmp_path: Path, payload: str, call: str
) -> None:
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    assert lines == []


@pytest.mark.parametrize("call", ["clean", "land"])
def test_the_reader_contract_stays_silent_when_there_is_no_fact(
    tmp_path: Path, call: str
) -> None:
    out, lines = run_clean(tmp_path, payload=json.dumps({"ok": True}), call=call)
    assert "rc=0" in out
    assert lines == []


@pytest.mark.parametrize("payload", ["", "{not json\n", "[1, 2, 3]\n"])
def test_the_reader_contract_pairs_silence_with_a_speaking_file(
    tmp_path: Path, payload: str
) -> None:
    # A count of passes over silent cases proves nothing on its own, so each one
    # is paired with the same reader speaking over a well-formed file.
    silent_out, silent_lines = run_clean(tmp_path / "silent", payload)
    assert "rc=0" in silent_out
    assert silent_lines == []
    speaking = (
        json.dumps(
            {"ok": True, "warning": "commit of run state failed: git exited 128"}
        )
        + "\n"
    )
    speaking_out, speaking_lines = run_clean(tmp_path / "speaking", speaking)
    assert "rc=0" in speaking_out
    assert speaking_lines == [
        "fixture-time leftover run state continued in a degraded state: "
        "commit of run state failed: git exited 128"
    ]


@pytest.mark.parametrize("call", ["clean", "land"])
def test_an_oversized_result_file_is_refused_out_loud(
    tmp_path: Path, call: str
) -> None:
    # 256 KiB is the ceiling. A file above it is refused with one line that names
    # the file, and the function still answers 0.
    payload = json.dumps({"ok": True, "warning": "commit of run state failed " * 10000})
    assert len(payload.encode()) > 262144, len(payload.encode())
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    name = (
        "run-state-leftovers.json" if call == "land" else "phase-100020-run-state.json"
    )
    label = (
        "phase 100020 closing run state" if call == "clean" else "leftover run state"
    )
    assert lines == [
        f"fixture-time {label} result file {tmp_path}/root/run/{name} is over the "
        "256 KiB ceiling; it says nothing about the run"
    ]


def test_a_missing_result_file_stays_silent(tmp_path: Path) -> None:
    # A clean run may legitimately write no file, so a missing path says nothing.
    out, lines = run_clean(tmp_path, "", call="missing")
    assert "rc=0" in out
    assert lines == []


def test_a_non_regular_result_path_is_refused_out_loud(tmp_path: Path) -> None:
    out, lines = run_clean(tmp_path, "", call="nonregular")
    assert "rc=0" in out
    assert lines == [
        f"fixture-time leftover run state result file {tmp_path}/root/run is not a "
        "regular file; it says nothing about the run"
    ]


def test_the_same_payload_on_two_ticks_writes_one_line(tmp_path: Path) -> None:
    payload = (
        json.dumps(
            {"ok": True, "warning": "commit of run state failed: git exited 128"}
        )
        + "\n"
    )
    out, lines = run_clean(tmp_path, payload, call="repeat")
    assert "rc=0" in out
    assert lines == [
        "fixture-time leftover run state continued in a degraded state: "
        "commit of run state failed: git exited 128"
    ]


def test_a_changed_payload_on_the_second_tick_writes_a_second_line(
    tmp_path: Path,
) -> None:
    first = json.dumps({"ok": True, "warning": "git exited 128"}) + "\n"
    second = json.dumps({"ok": True, "warning": "git exited 141"}) + "\n"
    out, lines = run_clean(tmp_path, first, call="repeat", payload2=second)
    assert "rc=0" in out
    assert lines == [
        "fixture-time leftover run state continued in a degraded state: git exited 128",
        "fixture-time leftover run state continued in a degraded state: git exited 141",
    ]


def test_a_result_just_under_the_ceiling_still_logs_its_warning(
    tmp_path: Path,
) -> None:
    payload = json.dumps({"ok": True, "warning": "commit of run state failed " * 8000})
    assert len(payload.encode()) < 262144, len(payload.encode())
    out, lines = run_clean(tmp_path, payload, "land")
    assert "rc=0" in out
    assert len(lines) == 1, lines
    assert "continued in a degraded state" in lines[0]


def test_a_failed_run_keeps_its_existing_line_and_returns_zero(tmp_path: Path) -> None:
    out, lines = run_clean(
        tmp_path, json.dumps({"ok": False}), call="land", exit_code=1
    )
    assert "rc=0" in out
    assert len(lines) == 1, lines
    assert "could not land leftover run state before launch (rc=1;" in lines[0]
