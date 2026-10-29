# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A control fault that breaks the driver names the self-test's FAIL lines."""

from __future__ import annotations

import guard_faults_engine as engine

CONTROL = {"expect": None, "expect_absent": "driver self-test: pass"}


def test_a_failed_control_fault_prints_the_fail_lines_and_their_detail():
    lines = [
        "ok: launched a session",
        "FAIL: expected one start notification, got 0",
        "  driver log: coordination state unavailable",
        "case time: launch 1.0 s",
        "driver self-test: FAIL",
    ]
    caught, detail = engine.judge(lines, 1, CONTROL)
    assert caught is False
    assert "the verdict was 'driver self-test: FAIL'" in detail
    assert "FAIL: expected one start notification, got 0" in detail
    assert "driver log: coordination state unavailable" in detail
    assert "case time" not in detail


def test_the_printed_fail_lines_are_bounded():
    lines = [f"FAIL: check {n}" for n in range(100)] + ["driver self-test: FAIL"]
    _caught, detail = engine.judge(lines, 1, CONTROL)
    assert detail.count("FAIL: check") == engine.FAIL_TAIL
    assert "FAIL: check 99" in detail


def test_a_working_driver_under_a_control_fault_still_passes():
    caught, _detail = engine.judge(["driver self-test: pass"], 0, CONTROL)
    assert caught is True
