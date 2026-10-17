# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair session reads the checkpoint's action conservatively.

Only `pushed` and `committed-and-pushed` describe a push; every other answer,
including a skipped checkpoint and one with no action at all, logs the
nothing-pushed line rather than claiming one.
"""

from pathlib import Path

from test_repair_session import read, run, setup


def test_a_skipped_checkpoint_is_not_read_as_a_push(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "checkpoint-skipped"})
    run(tmp_path, "run_repair_session 7-1 /t/task.md b")
    log = " ".join(read(tmp_path, "log"))
    assert "the run pushed nothing to b" in log
    assert "pushed to b" not in log


def test_a_checkpoint_without_an_action_is_not_read_as_a_push(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True})
    run(tmp_path, "run_repair_session 7-1 /t/task.md b")
    log = " ".join(read(tmp_path, "log"))
    assert "the run pushed nothing to b" in log
    assert "pushed to b" not in log
