# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The public maintenance command runs a real, halted tick without a board."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from config_fixture import BASE, PHASES_LOCAL, write

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver/phase-driver.sh"


def test_public_maintenance_mode_settles_a_halted_idle_tick(tmp_path: Path) -> None:
    config = copy.deepcopy(BASE)
    config.update({"repo-root": ".", "phases": {**PHASES_LOCAL, "repo": "project"}})
    config["commands"]["python"] = sys.executable
    (tmp_path / "plans").mkdir()
    (tmp_path / "pipeline.yaml").write_text(
        "run_dir: runs/phase-{phase_number:06d}\n"
        "inputs:\n  phase_number: {type: int}\n  phase_file: {}\n"
    )
    state = tmp_path / "runs/state"
    state.mkdir(parents=True)
    halt = state / "halted"
    halt.write_text("manual hold\n")
    path = write(tmp_path, json.dumps(config))
    result = subprocess.run(
        ["bash", str(DRIVER), "--config", str(path), "--merge-check-only"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert halt.read_text() == "manual hold\n"
    assert (state / "phase-tidy.json").exists()
    assert "merge check: ok (0 phase(s) examined)" in (state / "driver.log").read_text()
    assert not (state / "selection.err").exists()
    assert not (state / "claim").exists()
