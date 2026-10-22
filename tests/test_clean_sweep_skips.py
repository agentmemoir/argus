# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A whole-tree clean that lands nothing is not a success.

The sweep computed ``ok`` from the phases it landed, so a sweep that skipped
every phase had an empty list, answered ``ok`` with exit 0, and the driver
logged nothing while the leftovers stayed dirty.
"""

from pathlib import Path

import pytest

from argus.gitsync.mode_clean import mode_clean
from argus.gitsync.modes import result_exit_code
from test_driver_write_fences import PHASE, _claim, _foreign, _head, _repo


def test_a_sweep_that_skips_every_phase_is_not_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    _foreign(root)
    (root / "runs" / f"phase-{PHASE}" / "ledger.json").write_text("{}\n")
    before = _head(root)
    out = mode_clean(root)
    assert out["ok"] is False
    assert result_exit_code(out) == 1
    assert out["repos"] == []
    skip = out["skipped"][0]
    assert skip["phase"] == f"{PHASE:06d}"
    assert skip["state"] == "FENCED"
    # The summary is the line a caller logs, so it names the phase, the
    # state, and the reason, not only the phase.
    assert f"{PHASE:06d} (FENCED: {skip['error']})" in out["summary"]
    assert _head(root) == before
