#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Tests for explicit Argus settings and paths."""

import json
import tempfile
import unittest
from pathlib import Path

from argus import (
    conventions,
    phase_notifications,
    phase_policy,
    phase_reservations,
    roadmap_source,
    work_source,
)


class ConventionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name).resolve() / "project"
        self.repo.mkdir()
        self.board = self.repo / "vault" / "cedar-notes"
        self.board.mkdir(parents=True)
        self.pipeline = self.repo / "pipeline.yaml"
        self.settings = {
            "repo-root": self.repo,
            "pipeline": None,
            "roadmap-dir": Path("plans"),
            "number-input": "phase_number",
            "file-input": "phase_file",
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": Path("runs"),
            "machine.id": "machine-1",
            "board.repo": Path("vault/cedar-notes"),
            "board.file": Path("claims.json"),
            "board.branch": "fixture-claims",
            "board.remote": "origin",
            "commands.git": "git",
        }
        previous = conventions.configure(self.settings)
        self.addCleanup(conventions.configure, previous)

    def test_explicit_settings_control_board_and_run_paths(self):
        self.assertEqual(conventions.board_repo(self.repo), self.board)
        self.assertEqual(conventions.runs_dir(self.repo), self.repo / "runs")
        self.assertEqual(
            conventions.phase_run_dir(self.repo, 42), self.repo / "runs/phase-000042"
        )

    def test_include_and_exclude_settings_select_work_items(self):
        roadmap = self.repo / "plans"
        roadmap.mkdir()
        phase = roadmap / "phase-100042-api.md"
        excluded = roadmap / "phase-100043-skip.md"
        for path in (phase, excluded):
            path.write_text("### Decision Gates\nnone\n", encoding="utf-8")
        conventions.configure(
            {
                **self.settings,
                "driver.include": ["phase-*.md", "work-*.md"],
                "driver.exclude": [excluded.name],
            }
        )
        self.assertEqual(conventions.phase_globs(), ["phase-*.md", "work-*.md"])
        self.assertEqual(
            roadmap_source.candidates(self.repo, roadmap), [(100042, phase)]
        )
        self.assertEqual(conventions.resolve_phase_file(self.repo, 100042), phase)
        self.assertTrue(conventions.phase_excluded(excluded.name))
        self.assertFalse(conventions.phase_excluded(phase.name))

    def test_configured_patterns_drive_gate_report(self):
        roadmap = self.repo / "plans"
        roadmap.mkdir()
        (roadmap / "phase-100042-api.md").write_text(
            "### Decision Gates\nnone\n", encoding="utf-8"
        )
        (roadmap / "other-100043-api.md").write_text(
            "### Decision Gates\nnone\n", encoding="utf-8"
        )
        conventions.configure({**self.settings, "driver.include": ["phase-*.md"]})
        self.assertEqual(list(phase_policy.scan_gates(roadmap)), ["100042"])

    def test_pipeline_controls_typed_inputs_and_every_run_path(self):
        self.pipeline.write_text(
            "run_dir: vault/cedar-notes/history/task-{item:04d}-{label}\n"
            "inputs:\n  item: {type: int, required: true}\n"
            "  spec: {required: true}\n  label: {default: cedar}\n",
            encoding="utf-8",
        )
        phase = self.repo / "plans/milestone-42-api.md"
        phase.parent.mkdir()
        phase.write_text("task\n", encoding="utf-8")
        conventions.configure(
            {
                **self.settings,
                "pipeline": self.pipeline,
                "number-input": "item",
                "file-input": "spec",
                "driver.include": ["milestone-*.md"],
            }
        )
        expected = self.repo / "vault/cedar-notes/history/task-0042-cedar"
        self.assertEqual(
            conventions.pipeline_inputs(self.repo, 42, phase),
            {"item": 42, "spec": "plans/milestone-42-api.md", "label": "cedar"},
        )
        self.assertEqual(conventions.phase_run_dir(self.repo, 42, phase), expected)
        self.assertEqual(
            phase_reservations.reservation_file(self.repo, 42),
            expected / "reservation.json",
        )
        self.assertEqual(
            phase_reservations.publication_file(self.repo, 42),
            expected / "publication.json",
        )
        self.assertEqual(
            phase_notifications.default_run_json(phase), expected / "run.json"
        )
        expected.mkdir(parents=True)
        (expected / "run.json").write_text(
            json.dumps({"state": "completed"}), encoding="utf-8"
        )
        self.assertTrue(roadmap_source.run_completed(self.repo, 42, phase))

    def test_bad_pipeline_path_fails_without_fallback(self):
        conventions.configure({**self.settings, "pipeline": self.repo / "missing.yaml"})
        with self.assertRaisesRegex(ValueError, "cannot read pipeline"):
            conventions.phase_run_dir(self.repo, 42)

    def test_board_path_is_configured_and_must_stay_inside_board(self):
        self.assertEqual(phase_reservations.state_rel(), Path("claims.json"))
        for path in ("../outside.json", "/tmp/outside.json", ".git/config", ""):
            with self.subTest(path=path):
                conventions.configure({**self.settings, "board.file": Path(path)})
                with self.assertRaises(phase_reservations.CoordinationError):
                    phase_reservations.state_rel()
        outside = self.repo.parent / "outside"
        outside.mkdir()
        (self.board / "linked").symlink_to(outside, target_is_directory=True)
        conventions.configure(
            {**self.settings, "board.file": Path("linked/state.json")}
        )
        with self.assertRaises(phase_reservations.CoordinationError):
            phase_reservations.state_path(self.board)

    def test_settings_override_is_explicit_and_reversible(self):
        previous = conventions.override({"machine.id": "fixture-host"})
        self.assertEqual(conventions.setting("machine.id"), "fixture-host")
        conventions.configure(previous)
        self.assertEqual(conventions.setting("machine.id"), "machine-1")

    def test_registry_imports_the_package_implementation(self):
        self.assertIs(
            work_source.implementation("roadmap"), roadmap_source.RoadmapSource
        )
        work_source.self_test()


if __name__ == "__main__":
    unittest.main()
