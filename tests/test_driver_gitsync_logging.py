# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Driver log shows phases skipped by the whole-tree clean sweep (issue #172).

``gitsync --mode clean`` over every phase lands some and skips others and still
exits 0, so the driver's own exit-code line cannot carry a skip. The skipped
entry names the reason, and the driver has to say it or the operator never
learns why leftovers stayed dirty. The real ``log()`` writes here: the shared
maintenance harness replaces it, which would make the assertion vacuous.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src" / "argus" / "driver"

PRELUDE = """\
set -euo pipefail
export DRY_RUN=0 REPO="$ROOT" RUN_DIR="$ROOT/runs" LOG="$ROOT/driver.log"
ARGUS="$ROOT/argus"
REAL_PYTHON="$TEST_PYTHON"
ARGUS_CONFIG_ARGS=(--config c.jsonc)
GITSYNC_KIND_ARGS=()
mkdir -p "$RUN_DIR"
. "$DRIVER/lib-driver-log.sh"
. "$DRIVER/lib-driver-run.sh"
python_transport() {
  if [ "$1" = "-c" ]; then
    "$REAL_PYTHON" "$@"
    return $?
  fi
  printf '%s' "${GITSYNC_OUT:-}" >"$RUN_DIR/run-state-leftovers.json"
  return "${GITSYNC_RC:-0}"
}
PYTHON=python_transport
"""

BODY = """\
land_rc=0
gitsync_land_leftovers || land_rc=$?
printf 'land_rc=%s\\n' "$land_rc"
"""

SKIPPED = [
    {
        "phase": "100020",
        "state": "FENCED",
        "error": "phase 100020 is held by a live claim of another machine",
    },
    {
        "phase": "100031",
        "state": "COORDINATION_UNAVAILABLE",
        "error": "cannot read coordination state",
    },
]


def _run(tmp_path: Path, output: str, rc: int = 0) -> tuple[str, list[str]]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DRIVER": str(DRIVER),
        "ROOT": str(tmp_path),
        "TEST_PYTHON": sys.executable,
        "GITSYNC_OUT": output,
        "GITSYNC_RC": str(rc),
    }
    done = subprocess.run(
        ["bash", "-c", PRELUDE + BODY],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    log_path = tmp_path / "driver.log"
    lines = (
        log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    )
    return done.stdout, lines


def test_each_skipped_phase_is_logged_once_and_rc_stays_zero(
    tmp_path: Path,
) -> None:
    payload = {"ok": True, "mode": "clean", "state": "PARTIAL", "skipped": SKIPPED}
    stdout, lines = _run(tmp_path, json.dumps(payload))
    assert "land_rc=0" in stdout
    skip_lines = [line for line in lines if "leftover run state skipped phase" in line]
    assert len(skip_lines) == len(SKIPPED)
    for entry in SKIPPED:
        matches = [line for line in skip_lines if entry["phase"] in line]
        assert len(matches) == 1
        assert entry["state"] in matches[0]
        assert entry["error"] in matches[0]


def test_clean_sweep_logs_no_skip_line(tmp_path: Path) -> None:
    payload: dict[str, object] = {
        "ok": True,
        "mode": "clean",
        "repos": [],
        "pathspec": "runs",
        "summary": "clean sync ok: no leftover run state",
    }
    _stdout, lines = _run(tmp_path, json.dumps(payload))
    assert not [line for line in lines if "skipped phase" in line]


def test_failed_sweep_keeps_existing_line_and_returns_zero(tmp_path: Path) -> None:
    stdout, lines = _run(tmp_path, json.dumps({"ok": False, "mode": "clean"}), rc=1)
    assert "land_rc=0" in stdout
    assert any(
        "could not land leftover run state before launch (rc=1" in line
        for line in lines
    )


def test_unreadable_result_is_logged_and_rc_stays_zero(tmp_path: Path) -> None:
    stdout, lines = _run(tmp_path, "{not json")
    assert "land_rc=0" in stdout
    expected = (
        f"could not read the clean sweep result at "
        f"{tmp_path / 'runs' / 'run-state-leftovers.json'}; skipped phases are unknown"
    )
    assert any(line.endswith(expected) for line in lines)


def test_empty_result_file_is_logged_as_unreadable(tmp_path: Path) -> None:
    stdout, lines = _run(tmp_path, "")
    assert "land_rc=0" in stdout
    assert any(line.endswith("skipped phases are unknown") for line in lines)


def test_control_characters_are_removed_and_the_reason_is_capped(
    tmp_path: Path,
) -> None:
    reason = "a\nb\tc\rd\x1b[2K\x1b[31mRED\x07e" + "x" * 5000
    payload = {
        "ok": True,
        "mode": "clean",
        "state": "PARTIAL",
        "skipped": [{"phase": "100040", "state": "FENCED", "error": reason}],
    }
    _stdout, lines = _run(tmp_path, json.dumps(payload))
    skip_lines = [line for line in lines if "leftover run state skipped phase" in line]
    assert len(skip_lines) == 1
    assert "\x1b" not in skip_lines[0]
    assert "\x07" not in skip_lines[0]
    # The line is the driver's 25-byte timestamp, the 50-byte message prefix, the
    # 500-byte cap, and cap_length's 26-byte truncation marker: 601 bytes here,
    # against over 5000 for the uncapped reason.
    assert "[+4521 bytes truncated]" in skip_lines[0]
    assert len(skip_lines[0]) <= 620
