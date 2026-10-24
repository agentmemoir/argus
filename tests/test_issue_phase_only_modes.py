# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Phase-only gitsync modes refuse an issue run by name, before any phase lookup."""

from argparse import Namespace

import pytest

from argus.gitsync.git import ConfigError
from argus.gitsync.issue_modes import issue_mode


@pytest.mark.parametrize("mode", ["signals", "signal-reply", "peek-ci"])
def test_phase_only_mode_refuses_issue_kind(mode: str) -> None:
    args = Namespace(mode=mode, root=".", phase="255", phases=None, group=None)
    with pytest.raises(ConfigError, match=f"--mode {mode} is phase-only"):
        issue_mode(args)
