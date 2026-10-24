# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A refused launch writes the board's reason to the driver log."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIM_LIB = ROOT / "src/argus/driver/lib-driver-session-claim.sh"

# The board answer is a stub script, so no board is read here. The refusal path
# logs the answer and returns 1, which is all this test checks.
SCRIPT = r"""
source "$1"
log() { printf 'log: %s\n' "$*"; }
PYTHON="$PY"
ARGUS="$STUBS"
REPO="$STUBS"
PHASE_MACHINE_ID=machine-1
CLIO_RESERVATION_ID=rsv-1
CLIO_RESERVATION_GENERATION=1
ARGUS_CONFIG_ARGS=(--config config.json)
BOARD_KIND_ARGS=()
work_kind=phase
session_claim_ensure 100010 phase.md
echo "status=$?"
"""

BOARD_ANSWER = (
    "import json, sys\n"
    "print(json.dumps({'state': 'FENCED', 'error': "
    "'phase 100010 is no longer owned by this reservation'}, indent=2))\n"
    "sys.exit(1)\n"
)


def test_a_refused_claim_logs_the_board_reason(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    (stubs / "board").mkdir(parents=True)
    (stubs / "board" / "reservations.py").write_text(BOARD_ANSWER, encoding="utf-8")
    done = subprocess.run(
        ["bash", "-c", SCRIPT, "bash", str(CLAIM_LIB)],
        env={"PY": sys.executable, "STUBS": str(stubs), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "status=1" in done.stdout
    refusals = [
        line
        for line in done.stdout.splitlines()
        if line.startswith("log: claim check refused")
    ]
    assert len(refusals) == 1, done.stdout
    assert refusals[0].startswith("log: claim check refused phase 100010: ")
    assert "no longer owned by this reservation" in refusals[0]
