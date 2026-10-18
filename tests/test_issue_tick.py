# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A phase machine with no board is refused cleanly, never with a traceback."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACHINE = "tick-machine"


def test_phase_machine_without_a_board_still_refuses_cleanly() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/argus/select/next_phase.py"),
            "--config",
            str(ROOT / "examples/argus-config.jsonc"),
            "--source",
            "phases",
            "--server",
            "--machine-id",
            MACHINE,
            "--json",
            "--read-only",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout) == {
        "error": "reservation board repository is not configured",
        "state": "COORDINATION_UNAVAILABLE",
    }
