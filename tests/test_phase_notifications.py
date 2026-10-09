#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Tests for phase-driver notification formatting."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.phase_notifications import (
    default_run_json,
    display_title,
    format_duration,
    format_notification,
    phase_label,
)
from argus import conventions


class PhaseNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        previous = conventions.configure(
            {
                "repo-root": root,
                "pipeline": None,
                "driver.runs": root / "records",
            }
        )
        self.addCleanup(conventions.configure, previous)

    def test_title_preserves_common_capitalizations(self) -> None:
        self.assertEqual(
            phase_label("phase-100520-scale-ceilings-docs.md"),
            "Phase 100520 (Scale Ceilings Docs)",
        )
        self.assertEqual(
            phase_label("phase-100364-mcp-http-auto-bind-port-scan.md"),
            "Phase 100364 (MCP HTTP Auto Bind Port Scan)",
        )
        self.assertEqual(
            display_title("full-cli-confirmed-memory-mutations"),
            "Full CLI Confirmed Memory Mutations",
        )

    def test_started_is_a_short_update(self) -> None:
        self.assertEqual(
            format_notification("phase-100520-scale-ceilings-docs.md", "started"),
            "Phase 100520 (Scale Ceilings Docs) has started.",
        )

    def test_completed_without_a_run_record_stays_short(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "completed",
            run_json=Path("/nonexistent/run.json"),
        )
        self.assertEqual(message, "Phase 100520 (Scale Ceilings Docs) has completed.")

    def test_completed_lists_step_durations_and_total(self) -> None:
        events = [
            {"step": "developer", "visits": 1, "duration_s": 1130.855},
            {"step": "adversary", "visits": 1, "duration_s": 1059.11},
            {"step": "remediator", "visits": 1, "duration_s": 939.22},
            {"step": "approver", "visits": 1, "duration_s": 636.953},
            {"step": "remediator", "visits": 2, "duration_s": 953.683},
            {"step": "approver", "visits": 2, "duration_s": 395.61},
            {"step": "finalize", "visits": 1, "duration_s": 347.465},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "run.json"
            run_json.write_text(json.dumps({"events": events}), encoding="utf-8")
            message = format_notification(
                "phase-100740-explain-trace-projection.md",
                "completed",
                run_json=run_json,
            )
        self.assertEqual(
            message,
            "Phase 100740 (Explain Trace Projection) has completed.\n\n"
            "Developer r1 - 18 minutes 51 seconds\n"
            "Adversary r1 - 17 minutes 39 seconds\n"
            "Remediator r1 - 15 minutes 39 seconds\n"
            "Approver r1 - 10 minutes 37 seconds\n"
            "Remediator r2 - 15 minutes 54 seconds\n"
            "Approver r2 - 6 minutes 36 seconds\n"
            "Finalizer r1 - 5 minutes 47 seconds\n\n"
            "Total: 91.05 minutes",
        )

    def test_run_record_without_usable_durations_stays_short(self) -> None:
        events = [{"step": "developer"}, {"step": "adversary", "duration_s": None}]
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "run.json"
            run_json.write_text(json.dumps({"events": events}), encoding="utf-8")
            message = format_notification(
                "phase-100520-scale-ceilings-docs.md",
                "completed",
                run_json=run_json,
            )
        self.assertEqual(message, "Phase 100520 (Scale Ceilings Docs) has completed.")

    def test_run_record_skips_bad_events_and_defaults_the_visit(self) -> None:
        events = [
            "not an event",
            {"step": "developer", "duration_s": 20.5},
            {"step": "finalize", "visits": 0, "duration_s": 90},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "run.json"
            run_json.write_text(json.dumps({"events": events}), encoding="utf-8")
            message = format_notification(
                "phase-100520-scale-ceilings-docs.md",
                "completed",
                run_json=run_json,
            )
        self.assertEqual(
            message,
            "Phase 100520 (Scale Ceilings Docs) has completed.\n\n"
            "Developer r1 - 20.5 seconds\n"
            "Finalizer r1 - 1 minute 30 seconds\n\n"
            "Total: 1.84 minutes",
        )

    def test_duration_format_ranges(self) -> None:
        self.assertEqual(format_duration(48.6), "48.6 seconds")
        self.assertEqual(format_duration(15 * 60 + 22), "15 minutes 22 seconds")
        self.assertEqual(format_duration(12 * 3600 + 15 * 60), "12 hours 15 minutes")
        self.assertEqual(format_duration(61), "1 minute 1 second")
        self.assertEqual(format_duration(59.6), "1 minute")
        self.assertEqual(format_duration(900), "15 minutes")
        self.assertEqual(format_duration(3600), "1 hour")
        self.assertEqual(format_duration(2 * 3600 + 29 * 60), "2 hours 29 minutes")

    def test_default_run_json_points_at_the_phase_run_dir(self) -> None:
        path = default_run_json("phase-100740-explain-trace-projection.md")
        self.assertEqual(
            path.parent, conventions.setting("driver.runs") / "phase-100740"
        )
        self.assertEqual(path.name, "run.json")

    def test_run_state_folder_comes_from_loaded_settings(self) -> None:
        path = default_run_json("phase-100740-explain-trace-projection.md")
        self.assertEqual(path.parent.parent, conventions.setting("driver.runs"))
        self.assertEqual(path.parent.name, "phase-100740")

    def test_interrupted_reason_is_brief_and_redacted(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "interrupted",
            reason="the operator stopped the run\nTraceback: hidden",
        )
        self.assertIn("was interrupted because the operator stopped the run.", message)
        self.assertNotIn("Traceback", message)
        self.assertIn("resume on the next driver tick", message)

        secret_message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "interrupted",
            reason="token=secret-value",
        )
        self.assertNotIn("secret-value", secret_message)

    def test_stopped_includes_exit_code_and_run_command(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "stopped",
            exit_code=2,
            run_json=Path("records/task-100520/run.json"),
        )
        self.assertIn("Phase 100520 (Scale Ceilings Docs) stopped.", message)
        self.assertIn("exit code 2", message)
        self.assertIn(
            "ls -la records/task-100520",
            message,
        )

    def test_escalated_says_the_line_continues_and_names_the_record(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "escalated",
        )
        self.assertIn("exhausted its remedy rounds", message)
        self.assertIn("line continues", message)
        self.assertNotIn("halted", message)
        self.assertIn(
            "cat "
            + str(
                conventions.setting("driver.runs")
                / "phase-100520/remedy-escalated.json"
            ),
            message,
        )

    def test_stalled_uses_friendly_duration(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "stalled",
            quiet_minutes=45,
        )
        self.assertIn("no new output for 45 minutes", message)
        self.assertIn("session is still running", message)

    def test_released_says_the_group_returns_to_the_queue(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "released",
        )
        self.assertEqual(
            message,
            "Phase 100520 (Scale Ceilings Docs) was released. "
            "Its group returned to the queue; another agent may pick it up.",
        )

    def test_group_clause_names_every_member(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "started",
            group=["100520", "100521", "100522"],
        )
        self.assertEqual(
            message,
            "Phase 100520 (Scale Ceilings Docs) (group: 100520, 100521, 100522) "
            "has started.",
        )

    def test_group_accepts_a_comma_separated_string(self) -> None:
        message = format_notification(
            "phase-100520-scale-ceilings-docs.md",
            "released",
            group="100520, 100521",
        )
        self.assertIn("(group: 100520, 100521)", message)
        self.assertIn("was released", message)

    def test_no_group_leaves_the_message_unchanged(self) -> None:
        self.assertEqual(
            format_notification("phase-100520-scale-ceilings-docs.md", "started"),
            format_notification(
                "phase-100520-scale-ceilings-docs.md", "started", group=[]
            ),
        )


if __name__ == "__main__":
    unittest.main()
