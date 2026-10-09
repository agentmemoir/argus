# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The plain roadmap queue: which phases exist, and which one comes next."""

from argus.conventions import phase_file_parts, phase_paths
from argus.roadmap.evidence import index_complete, is_done
from argus.select.policy import is_runnable_phase, phase_gates_blocked


def candidates(_repo, roadmap):
    out = []
    if roadmap is None:
        return out
    for path in phase_paths(roadmap):
        parts = phase_file_parts(path)
        if parts and is_runnable_phase(parts[0]):
            out.append((parts[0], path))
    return sorted(out)


def select_next(repo, roadmap):
    index_done = index_complete(repo, roadmap)
    for number, path in candidates(repo, roadmap):
        if not is_done(repo, number, path, index_done):
            return number, path
    return None


def gate_block(number, path):
    """Return a BLOCKED_ON_GATE result when the plan owes the owner a decision.

    A phase with an ``open`` decision gate is not runnable, and the whole point
    of recording the gate in the plan is that this check finds it before a
    reservation is spent. Phase 100817 ran a full round and stopped on its own
    mandatory stop condition because the gate was real but the selector was not
    reading it. Fails closed: a plan whose gate section cannot be parsed is
    reported as blocked rather than launched, because an unreadable gate and an
    absent gate are the same risk.
    """
    try:
        blocked = phase_gates_blocked(path)
    except OSError:
        return {
            "state": "BLOCKED_ON_GATE",
            "phase": f"{number:06d}",
            "path": str(path),
            "error": "decision gate section could not be read",
        }
    if blocked:
        return {
            "state": "BLOCKED_ON_GATE",
            "phase": f"{number:06d}",
            "path": str(path),
            "error": "open decision gate; the owner must record it first",
        }
    return None
