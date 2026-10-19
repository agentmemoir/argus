# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The plain phases queue: which phases exist, and which one comes next."""

from pathlib import Path

from argus.conventions import phase_file_parts, phase_paths
from argus.phases.evidence import index_complete, is_done
from argus.select.policy import is_runnable_phase

_Phase = tuple[int, Path]


def candidates(_repo: str | Path, phases: str | Path | None) -> list[_Phase]:
    out: list[_Phase] = []
    if phases is None:
        return out
    for path in phase_paths(phases):
        parts = phase_file_parts(path)
        if parts and is_runnable_phase(parts[0]):
            out.append((parts[0], path))
    return sorted(out)


def select_next(repo: str | Path, phases: str | Path | None) -> _Phase | None:
    index_done = index_complete(repo, phases)
    for number, path in candidates(repo, phases):
        if not is_done(repo, number, path, index_done):
            return number, path
    return None
