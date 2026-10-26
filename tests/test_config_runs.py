# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The run folder: inside the board checkout by default, and refused outside it."""

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

from argus.config import cli as cfgmod
from config_fixture import BASE, check, expect_error, write

BOARD = {"repo": "board", "file": "claims.json", "branch": "main", "remote": "origin"}


def document_with_board(runs: str | None) -> dict[str, Any]:
    document: dict[str, Any] = copy.deepcopy(BASE)
    document["board"] = copy.deepcopy(BOARD)
    if runs is None:
        del document["driver"]["runs"]
    else:
        document["driver"]["runs"] = runs
    return document


def test_a_board_defaults_the_run_folder_inside_its_checkout() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(document_with_board(None)))
        base = Path(tmp).resolve()
        config = cfgmod.load(str(path))
        check(config.get("driver.runs") == base / "board" / "runs", config.values)
        check(config.source("driver.runs") == "default", config.source("driver.runs"))


def test_a_run_folder_inside_the_board_checkout_is_kept() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, json.dumps(document_with_board("board/state-runs")))
        base = Path(tmp).resolve()
        config = cfgmod.load(str(path))
        check(
            config.get("driver.runs") == base / "board" / "state-runs",
            config.get("driver.runs"),
        )
        check(config.source("driver.runs") == "file", config.source("driver.runs"))


def test_a_run_folder_outside_the_board_checkout_is_refused_by_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for runs in ("elsewhere", "board"):
            path = write(tmp, json.dumps(document_with_board(runs)))
            expect_error(
                lambda path=path: cfgmod.load(str(path)),
                "invalid value for key 'driver.runs'",
            )


def test_without_a_board_the_run_folder_is_still_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        document = copy.deepcopy(BASE)
        del document["driver"]["runs"]
        path = write(tmp, json.dumps(document))
        expect_error(
            lambda: cfgmod.load(str(path)), "missing required key 'driver.runs'"
        )
