# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check the CI repair round keys: defaults with no config, and file values."""

import copy
import json
from pathlib import Path

import pytest

from argus.config import cli as cfgmod
from argus.config.errors import ConfigError
from config_fixture import BASE, write


def test_repair_keys_default_without_config(tmp_path: Path) -> None:
    config = cfgmod.load(str(write(str(tmp_path), json.dumps(BASE))))
    assert config.get("repair.attempts") == 3
    assert config.get("repair.wait") == 600
    assert config.get("repair.pipeline") == tmp_path.resolve() / "repair.yaml"
    lines = cfgmod.shell_lines(config)
    assert "REPAIR_ATTEMPTS=3" in lines, lines
    assert "REPAIR_WAIT=600" in lines, lines


def test_repair_keys_read_from_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = copy.deepcopy(BASE)
    document["repair"] = {"pipeline": "flows/fix.yaml", "attempts": 2, "wait": 60}
    config = cfgmod.load(str(write(str(tmp_path), json.dumps(document))))
    assert config.get("repair.attempts") == 2
    assert config.get("repair.wait") == 60
    assert config.get("repair.pipeline") == tmp_path.resolve() / "flows/fix.yaml"
    assert "repair" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("key", "value"), [("attempts", 0), ("attempts", -1), ("wait", -1)]
)
def test_repair_keys_refuse_values_below_the_floor(
    tmp_path: Path, key: str, value: int
) -> None:
    document = copy.deepcopy(BASE)
    document["repair"] = {key: value}
    with pytest.raises(ConfigError, match=f"repair.{key}"):
        cfgmod.load(str(write(str(tmp_path), json.dumps(document))))
