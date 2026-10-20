# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The pre-launch claim renew: the last fence check before work starts."""

import subprocess
import sys
from pathlib import Path


def test_claim_start_running_stops_the_launch_when_the_renew_refuses(
    tmp_path: Path,
) -> None:
    """The pre-launch renew is the last fence check before work starts.

    A renew the board refuses means the board no longer covers this run, so
    the helper fails closed: it names the refusal with its action and returns
    non-zero, which is the signal the caller launches nothing on.
    """
    driver = Path(__file__).resolve().parents[1] / "src/argus/driver"
    script = f"""
set -euo pipefail
. "{driver}/lib-driver-session-claim.sh"
ARGUS="{driver.parent}"
PYTHON="{sys.executable}"
REPO="{tmp_path}"
PHASE_MACHINE_ID="selftest-machine"
work_kind=phase
log() {{ printf 'log: %s\\n' "$1"; }}
CLIO_RESERVATION_ID="rsv-nowhere"
CLIO_RESERVATION_GENERATION="3"
rc=0
session_claim_start_running 100010 || rc=$?
if [ "$rc" -eq 0 ]; then
  echo "FAIL: a refused renew let the launch continue" >&2
  exit 1
fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no launch" in result.stderr
    assert "Action:" in result.stderr
