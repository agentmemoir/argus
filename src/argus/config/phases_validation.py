# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Validate the phases repository: its folder must stay inside its checkout."""

from pathlib import Path
from typing import cast

from argus.config.errors import ConfigError


def validate_phases(values: dict[str, object]) -> None:
    """Refuse a phases folder that escapes the configured phases repository.

    The folder is the one place Argus reads phases from and writes the index to,
    so a value that resolves outside the checkout is refused here rather than
    discovered as a write into a stranger's tree later. The check mirrors the
    board's own containment rule for ``board.issues-file``.
    """
    repo = Path(cast("Path", values["phases.repo"])).resolve()
    directory = cast("str", values["phases.directory"])
    if not directory.strip():
        raise ConfigError(
            "invalid value for key 'phases.directory': use a safe "
            "repository-relative path"
        )
    folder = Path(directory)
    if folder.is_absolute():
        resolved = folder.resolve()
    else:
        if not folder.parts or ".." in folder.parts or folder.parts[0] == ".git":
            raise ConfigError(
                "invalid value for key 'phases.directory': use a safe "
                "repository-relative path"
            )
        resolved = (repo / folder).resolve()
    if not resolved.is_relative_to(repo):
        raise ConfigError(
            "invalid value for key 'phases.directory': must stay inside phases.repo"
        )
