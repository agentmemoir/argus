# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A Glide run takes its temporary folder from the run state, not the caller."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB_LIB = ROOT / "src/argus/driver/lib-driver-job.sh"

# The job library is sourced, its neighbours are stubbed, and the glide command is
# a shell function, so the test runs no program from a temporary folder.
SCRIPT = r"""
source "$1"
phase_run_dir() { printf '%s\n' "$STAGE_ROOT/phase"; }
terminal_state_of_file() { :; }
log() { printf 'log: %s\n' "$*"; }
fake_glide() { printf 'glide: TMPDIR=%s BUN_TMPDIR=%s\n' "$TMPDIR" "$BUN_TMPDIR"; }
CMD_GLIDE=fake_glide
PIPELINE=pipeline.yaml
CLIO_RESUME=1
number=1
rel=phase.md
glide_inputs=()
STAGE_TMP="$STAGE_ROOT/stage-tmp"
glide_run_once
"""


def test_glide_gets_the_stage_folder_as_tmpdir_and_bun_tmpdir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    stage = state / "stage-tmp"
    done = subprocess.run(
        ["bash", "-c", SCRIPT, "bash", str(JOB_LIB)],
        env={**os.environ, "STAGE_ROOT": str(state), "TMPDIR": str(tmp_path / "x")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert stage.is_dir()
    assert f"glide: TMPDIR={stage} BUN_TMPDIR={stage}" in done.stdout
    assert f"log: stage temp dir: {stage} (TMPDIR and BUN_TMPDIR for glide)" in (
        done.stdout
    )
