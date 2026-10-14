# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run-state predicates: what a run left behind, and whether it can continue.

`evidence` answers whether a phase is finished. These answer the other half of
the same question. A `blocked` run stopped at a stage and can still continue, so
it must stay out of the done set. A run directory with no readable `run.json` but
a `resume.json` is the interrupted class, and it is unfinished for the same
reason. An `abandoned.json` residue is a phase a person has parked.
"""

import json
from pathlib import Path
from typing import Any, cast

from argus.conventions import phase_run_dir

#: The run state glide writes when a run stops at a stage it can resume from.
BLOCKED = "blocked"
RESULT_FILENAME = "run.json"
RESUME_FILENAME = "resume.json"
ABANDONED_FILENAME = "abandoned.json"


def _run_dir(
    repo: str | Path, number: int, phase_file: str | Path | None
) -> Path | None:
    """The run directory, or None when the layout cannot be resolved.

    A templated pipeline whose template does not format raises `ValueError`.
    """
    try:
        return phase_run_dir(repo, number, phase_file)
    except (OSError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return cast("dict[str, Any]", value)


def _state_in(directory: Path) -> str | None:
    state = _read_json(directory / RESULT_FILENAME)
    if state is None:
        return None
    value = state.get("state")
    return value if isinstance(value, str) else None


def run_state(
    repo: str | Path, number: int, phase_file: str | Path | None = None
) -> str | None:
    """The run state glide recorded, or None when there is no readable record.

    `phase_file` is for a templated run layout.
    """
    directory = _run_dir(repo, number, phase_file)
    if directory is None:
        return None
    return _state_in(directory)


def run_unfinished(
    repo: str | Path, number: int, phase_file: str | Path | None = None
) -> bool:
    """Whether the run stopped somewhere it can still continue from.

    True for a `blocked` run, and for a run directory with no readable
    `run.json` but a `resume.json` beside it. Everything else was never started
    or is finished.
    """
    directory = _run_dir(repo, number, phase_file)
    if directory is None:
        return False
    state = _state_in(directory)
    if state == BLOCKED:
        return True
    return state is None and (directory / RESUME_FILENAME).is_file()


def run_abandoned(
    repo: str | Path, number: int, phase_file: str | Path | None = None
) -> bool:
    """Whether a person has parked this phase with an `abandoned.json` residue.

    A residue that is not version 1, or names another phase, is not this phase's.
    """
    directory = _run_dir(repo, number, phase_file)
    if directory is None:
        return False
    residue = _read_json(directory / ABANDONED_FILENAME)
    if residue is None or residue.get("version") != 1:
        return False
    return residue.get("phase") == str(number)
