# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The probe state of each candidate directory, for a caller that found none.

`make_exec_dir` returns an empty string and nothing more. This module walks the same
candidates and reports what each probe found there: runs, refused by the mount, or
unprobed. A candidate whose directory cannot be made is reported as unprobed too.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from argus.exec_tempdir import BUILD_TREE, CANDIDATE_PARENTS, _expand, _run_probe


def candidate_states(parents: Sequence[str | None] | None = None) -> list[str]:
    """Return one line per candidate: its label, then the state its probe reported."""
    states: list[str] = []
    for parent in CANDIDATE_PARENTS if parents is None else parents:
        base = _expand(parent)
        label = base or "default"
        try:
            if base == str(BUILD_TREE):
                Path(base).mkdir(parents=True, exist_ok=True)
            directory = tempfile.mkdtemp(prefix="argus-selftest-", dir=base)
        except OSError as exc:
            states.append(f"{label}: unprobed, no directory could be made ({exc})")
            continue
        try:
            states.append(f"{label}: {_run_probe(directory).value}")
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    return states
