# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Validate the phases repository: it is a Git work tree, and its folder is inside it."""

import subprocess
from pathlib import Path
from typing import cast

from argus.config.errors import ConfigError

# A configured git that does not answer in this many seconds is not a work tree
# this run can read, and a config load must not hang on it.
_GIT_TIMEOUT_S = 30


def validate_phases(values: dict[str, object]) -> None:
    """Refuse a phases folder that escapes its checkout, or a checkout that is not Git.

    The folder is the one place Argus reads phases from and writes the index to,
    so a value that resolves outside the checkout is refused here rather than
    discovered as a write into a stranger's tree later. The check mirrors the
    board's own containment rule for ``board.issues-file``.

    The repository itself must be a Git work tree: the folder is read from it
    and the index is committed and pushed through it, so a plain directory named
    as ``phases.repo`` is a setting mistake, refused here with its key named.
    """
    repo_value = values.get("phases.repo")
    directory_value = values.get("phases.directory")
    if repo_value is None or directory_value is None:
        return
    repo = Path(cast("Path", repo_value)).resolve()
    git = cast("str", values.get("commands.git", "git"))
    if not _is_work_tree(repo, git):
        raise ConfigError("invalid value for key 'phases.repo': not a Git work tree")
    directory = cast("str", directory_value)
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


def _is_work_tree(repo: Path, git: str) -> bool:
    """True when ``git`` reports ``repo`` as a work tree, false on any failure."""
    try:
        done = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"
