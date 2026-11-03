# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A refused repair launch is taken back once, even when the take-back fails midway."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.gitsync import repair_refusal
from argus.gitsync.repair_round import load_record


def test_a_refusal_is_taken_back_once_when_the_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The take-back fails after it saved the record. The next tick reads the record
    # and the refusal file again, so the round must not be taken back a second time.
    record_path = tmp_path / "repair-7.json"
    refused_path = tmp_path / "repair-7.refused"
    refused_path.write_text("H\n1600\nthe tmux window did not open\n", encoding="utf-8")
    record_path.write_text('{"head": "H", "attempts": [1000.0, 1600.0]}', "utf-8")
    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == refused_path:
            raise OSError("the refusal file cannot be removed")
        real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(OSError):
        repair_refusal.take(
            tmp_path, record_path, "7", "H", load_record(record_path), 1700.0
        )
    monkeypatch.setattr(Path, "unlink", real_unlink)

    repair_refusal.take(
        tmp_path, record_path, "7", "H", load_record(record_path), 1800.0
    )

    record = load_record(record_path)
    assert record["attempts"] == [1000.0]
    assert len(record["refused"]) == 1
    assert not refused_path.exists()
