# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The index write is committed and pushed to the configured phases branch.

The repositories here are plain git directories under `tmp_path`. Nothing is
executed from them: the transaction runs the configured git command.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus import conventions
from argus.phases.index import audit

INDEX = (
    "# Index\n\n"
    "| Slice | Title | Status |\n"
    "| --- | --- | --- |\n"
    "| 100155 | A phase | Plan ready |\n"
)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    remote = tmp_path / "phases.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(remote))
    repo = tmp_path / "phases-repo"
    git(tmp_path, "clone", "-q", str(remote), str(repo))
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    phases = repo / "plans"
    phases.mkdir()
    (phases / "index.md").write_text(INDEX, encoding="utf-8")
    (phases / "phase-100155-demo.md").write_text("## 100155. Demo\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "push", "-q", "origin", "master")
    runs = tmp_path / "runs"
    run = runs / "phase-100155"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"state": "completed"}), encoding="utf-8")
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "commands.git": "git",
            "repo-root": str(tmp_path),
            "phases.repo": str(repo),
            "phases.directory": "plans",
            "phases.branch": "master",
            "phases.remote": "origin",
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": str(runs),
        },
    )
    return {"repo": repo, "remote": remote, "phases": phases}


def test_index_annotation_lands_on_the_configured_branch(
    setup: dict[str, Path],
) -> None:
    issues = audit(setup["repo"], setup["phases"], apply_index=True)

    assert issues == 1
    pushed = git(setup["remote"], "show", "master:plans/index.md")
    assert "| 100155 | A phase | Complete" in pushed
    # The transaction writes in a detached checkout, so the live one is not
    # touched; only the phases branch carries the change.
    assert "Complete" not in (setup["phases"] / "index.md").read_text()
