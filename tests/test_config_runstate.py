# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The optional ``runstate`` config object: its resolved values and shell vars.

The object is optional as a whole, so the sample leaves it commented out. When
present, ``repo`` resolves like every other path, and the three keys carry the
shell variables the config CLI prints.
"""

import copy
import json
import tempfile
from pathlib import Path

from argus.config import cli as cfgmod
from config_fixture import BASE, check, write


def test_runstate_object_resolves_repo_remote_and_branch():
    with tempfile.TemporaryDirectory() as tmp:
        document = copy.deepcopy(BASE)
        document["runstate"] = {
            "repo": "runstate",
            "remote": "origin",
            "branch": "main",
        }
        path = write(tmp, json.dumps(document))
        base = Path(tmp).resolve()
        config = cfgmod.load(str(path))
        check(
            config.get("runstate.repo") == base / "runstate",
            config.get("runstate.repo"),
        )
        check(config.get("runstate.remote") == "origin", config.get("runstate.remote"))
        check(config.get("runstate.branch") == "main", config.get("runstate.branch"))
        check(config.source("runstate.repo") == "file", config.source("runstate.repo"))
        check(cfgmod.SHELL_VARS["runstate.repo"] == "RUNSTATE_REPO", cfgmod.SHELL_VARS)
        check(
            cfgmod.SHELL_VARS["runstate.remote"] == "RUNSTATE_REMOTE", cfgmod.SHELL_VARS
        )
        check(
            cfgmod.SHELL_VARS["runstate.branch"] == "RUNSTATE_BRANCH", cfgmod.SHELL_VARS
        )
