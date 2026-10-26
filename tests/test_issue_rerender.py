# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A claimed issue whose task file is gone is rendered again the first-selection way."""

from pathlib import Path

import pytest

from argus import conventions
from argus.issues.rerender import rerender_task
from issues_fixture import fake_gh, issue, settings


def test_rerender_writes_the_task_file_with_the_first_selection_renderer(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    try:
        path = rerender_task(tmp_path, 100001)
    finally:
        conventions.configure(previous)
    assert path.parent == tmp_path / "tasks"
    assert path.name.startswith("issue-100001")
    assert "# Issue 100001" in path.read_text(encoding="utf-8")
