# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A Glide launch makes the git hooks ready first, and a refusal stops the launch."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB_LIB = ROOT / "src/argus/driver/lib-driver-job.sh"

# The job library is sourced, its neighbours are stubbed, and glide is a shell
# function. The hook setup is a stub script, so no real hook runs here.
SCRIPT = r"""
source "$1"
phase_run_dir() { printf '%s\n' "$STAGE_ROOT/phase"; }
terminal_state_of_file() { :; }
log() { printf 'log: %s\n' "$*"; }
fake_glide() { printf 'glide: started\n'; }
CMD_GLIDE=fake_glide
PIPELINE=pipeline.yaml
CLIO_RESUME=1
number=1
rel=phase.md
glide_inputs=(--input ticket=1)
RUN_DIR="$STAGE_ROOT"
REPO="$STAGE_ROOT"
PYTHON="$PY"
ARGUS="$STUBS"
ARGUS_CONFIG_ARGS=(--config config.json)
glide_run_once
echo "status=$?"
"""

REFUSED = "import sys\nprint('Needs You. stub refusal')\nsys.exit(1)\n"
ACCEPTED = "print('hooks: present (stub)')\n"


def _launch(tmp_path: Path, stub_source: str) -> subprocess.CompletedProcess[str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "hooks_setup.py").write_text(stub_source, encoding="utf-8")
    env = {
        **os.environ,
        "STAGE_ROOT": str(tmp_path / "state"),
        "PY": sys.executable,
        "STUBS": str(stubs),
    }
    return subprocess.run(
        ["bash", "-c", SCRIPT, "bash", str(JOB_LIB)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def test_a_refused_checkout_stops_the_launch_and_says_why(tmp_path: Path) -> None:
    done = _launch(tmp_path, REFUSED)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "log: Needs You. stub refusal" in done.stdout
    assert "glide: started" not in done.stdout
    assert "status=1" in done.stdout


def test_an_accepted_checkout_launches(tmp_path: Path) -> None:
    done = _launch(tmp_path, ACCEPTED)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "glide: started" in done.stdout
    assert "status=0" in done.stdout
