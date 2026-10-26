# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local Git and GitHub fixtures for issue publication contracts."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.reservations import ReservationStore
from argus.gitsync.git import git
from argus.gitsync.selftest_fixture import _configure_self_test, _init_remote
from argus.issues.claims import save
from issues_fixture import fake_gh, fixture_update, issue


@dataclass
class Publication:
    root: Path
    private: Path
    remote: Path
    private_remote: Path
    store: ReservationStore
    github: Path

    def claim(
        self, number: int, slug: str = "fix-input"
    ) -> tuple[dict[str, Any], Path]:
        token = self.store.reserve(number, "worker-one")
        task = self.private / "tasks" / f"issue-{number:06d}-{slug}.md"
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text("FROZEN_TASK_SENTINEL\n", encoding="utf-8")
        save(self.root, number, token, task)
        return token, task

    def commit_code(self) -> str:
        (self.root / "code.txt").write_text("code change\n", encoding="utf-8")
        assert git(self.root, "add", "code.txt")[0] == 0
        assert git(self.root, "commit", "-qm", "fix input")[0] == 0
        return git(self.root, "rev-parse", "HEAD")[1]

    def github_state(
        self,
        number: int,
        branch: str,
        head: str,
        *,
        issue_state: str = "OPEN",
        pr_state: str = "OPEN",
    ) -> None:
        fixture_update(
            self.github,
            rows=[
                issue(
                    number,
                    state=issue_state,
                    closedByPullRequestsReferences=[{"number": 41}],
                )
            ],
            pr_states={"41": pr_state},
            pr_heads={"41": {"headRefName": branch, "headRefOid": head}},
        )


@pytest.fixture(name="publication")
def publication_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Publication:
    monkeypatch.setattr(conventions, "_settings", {})
    conventions.configure({"commands.git": "git"})
    remote, root = _init_remote(tmp_path, "public")
    private_remote, initial_private = _init_remote(tmp_path, "private")
    private = root / "vault"
    shutil.move(str(initial_private), private)
    github = tmp_path / "github"
    github.mkdir()
    binary = fake_gh(github, [])
    # Extend only this fixture's fake transport with the PR head fields tidy
    # verifies; production runs continue through the normal gh command.
    binary.write_text(
        binary.read_text().replace(
            'result = {"state": fixture.get("pr_states", {}).get(args[2], "CLOSED")}',
            'result = {"state": fixture.get("pr_states", {}).get(args[2], "CLOSED"), **fixture.get("pr_heads", {}).get(args[2], {})}',
        ),
        encoding="utf-8",
    )
    _configure_self_test(root, private)
    conventions.override(
        {
            "board.branch": "fixture-claims",
            "board.issues-file": "issue-claims.json",
            "issues.repo": "example/project",
            "issues.task-dir": private / "tasks",
            "commands.gh": str(binary),
        }
    )
    store = ReservationStore(root, issues=True)
    store.migrate_board()
    return Publication(root, private, remote, private_remote, store, github)
