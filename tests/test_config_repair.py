# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check the CI repair round keys: defaults with no config, and file values."""

import copy
import json
from pathlib import Path

from argus.config import cli as cfgmod
from config_fixture import BASE, write


def test_repair_keys_default_without_config(tmp_path: Path) -> None:
    config = cfgmod.load(str(write(str(tmp_path), json.dumps(BASE))))
    assert config.get("repair.attempts") == 3
    assert config.get("repair.wait") == 600
    assert config.get("repair.pipeline") == tmp_path.resolve() / "repair.yaml"
    lines = cfgmod.shell_lines(config)
    assert "REPAIR_ATTEMPTS=3" in lines, lines
    assert "REPAIR_WAIT=600" in lines, lines


def test_repair_keys_read_from_file(tmp_path: Path, capsys) -> None:
    document = copy.deepcopy(BASE)
    document["repair"] = {"pipeline": "flows/fix.yaml", "attempts": 2, "wait": 60}
    config = cfgmod.load(str(write(str(tmp_path), json.dumps(document))))
    assert config.get("repair.attempts") == 2
    assert config.get("repair.wait") == 60
    assert config.get("repair.pipeline") == tmp_path.resolve() / "flows/fix.yaml"
    assert "repair" not in capsys.readouterr().err
