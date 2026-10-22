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
sys.stdout.write(open(os.path.join(os.environ["TEST_ROOT"], "payload.json")).read())
sys.exit(int(os.environ.get("TEST_EXIT", "0")))
"""

SCRIPT = """
set -euo pipefail
for name in lib-driver-log lib-driver-run; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"
ARGUS="$TEST_ROOT/argus"; REPO="$TEST_ROOT/root"
RUN_DIR="$TEST_ROOT/root/run"; LOG="$RUN_DIR/events"
DRY_RUN=0; work_kind=phase
ARGUS_CONFIG_ARGS=(--config fixture.json); GITSYNC_KIND_ARGS=()
rc=0
case "$TEST_CALL" in
  clean) gitsync_clean 100020 || rc=$? ;;
  *) gitsync_land_leftovers || rc=$? ;;
esac
printf 'rc=%s\\n' "$rc"
"""


def run_clean(
    tmp_path: Path, payload: str, call: str = "land", exit_code: int = 0
) -> tuple[str, list[str]]:
    """Run one clean function over a fake main.py printing payload."""
    root = tmp_path / "root"
    (root / "run").mkdir(parents=True)
    (tmp_path / "argus" / "gitsync").mkdir(parents=True)
    (tmp_path / "argus" / "gitsync" / "main.py").write_text(FAKE_MAIN)
    (tmp_path / "payload.json").write_text(payload)
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
                "warning": "commit of run state failed: Author identity unknown\n\n"
                "*** Please tell me who you are.\n"
                "fatal: unable to auto-detect email address; the next tick retries",
            }
        )
        + "\n"
    )
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    assert len(lines) == 1, lines
    assert "continued in a degraded state" in lines[0]
    assert "commit of run state failed" in lines[0]


@pytest.mark.parametrize("call", ["clean", "land"])
@pytest.mark.parametrize("payload", ["", "{not json\n", "[1, 2, 3]\n"])
def test_an_empty_or_malformed_result_file_logs_nothing(
    tmp_path: Path, payload: str, call: str
) -> None:
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    assert lines == []


@pytest.mark.parametrize("call", ["clean", "land"])
def test_a_result_with_neither_warning_nor_skipped_logs_nothing(
    tmp_path: Path, call: str
) -> None:
    out, lines = run_clean(tmp_path, payload=json.dumps({"ok": True}), call=call)
    assert "rc=0" in out
    assert lines == []


@pytest.mark.parametrize("call", ["clean", "land"])
def test_an_oversized_result_file_logs_nothing(tmp_path: Path, call: str) -> None:
    # 256 KiB is the ceiling. A file above it is not a result this driver wrote,
    # so it says nothing and the function still answers 0.
    payload = json.dumps({"ok": True, "warning": "commit of run state failed " * 10000})
    assert len(payload.encode()) > 262144, len(payload.encode())
    out, lines = run_clean(tmp_path, payload, call)
    assert "rc=0" in out
    assert lines == []


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
