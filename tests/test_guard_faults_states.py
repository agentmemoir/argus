# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The NOT RUN report names each scratch candidate and what the host did with it.

Source: ``tests/guard_faults.py``. ``candidate_states`` reads the candidates that
``argus.exec_tempdir`` lists and reports the three states a probe can give.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import guard_faults


def test_candidate_states_names_a_missing_candidate_as_unprobed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing candidate cannot be probed, so it is unprobed, not refused."""
    missing = "/nonexistent-argus-candidate/for-a-test"
    monkeypatch.setattr(guard_faults, "candidate_bases", lambda: [missing])
    assert guard_faults.candidate_states() == [f"{missing}: unprobed"]


def test_candidate_states_probes_a_writable_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writable candidate is probed: its state is runs or refused, not unprobed."""
    monkeypatch.setattr(guard_faults, "candidate_bases", lambda: [str(tmp_path)])
    [line] = guard_faults.candidate_states()
    assert line in (f"{tmp_path}: runs", f"{tmp_path}: refused")
