# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A claimed no-change issue on a real board, driven through the real shell selection."""

import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.reservations import ReservationStore
from argus.gitsync.selftest_fixture import _init_remote
from argus.issues.claims import save
from config_fixture import BASE
from issues_fixture import GROUPS, argv, fake_gh, fixture_update, issue

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
LIBS = (
    "lib-driver-log.sh",
    "lib-driver-select.sh",
    "lib-driver-issues.sh",
    "lib-driver-config.sh",
    "lib-driver-group.sh",
    "lib-driver-select-flow.sh",
    "lib-driver-select-real.sh",
    "lib-driver-maintenance.sh",
    "lib-driver-run.sh",
    "lib-driver-job.sh",
)
DONE = 100001
NEXT = 100002
KEY = f"{DONE:06d}"
TASK = f"issue-{KEY}-fix-the-input.md"
BRANCH = f"issue/{DONE}-fix-the-input"
HELD = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy", "accepted")]
FREE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
REASON = "The flag already exists in crates/cli/src/main.rs."
OLD_NOTICE = (
    f"Needs You. Issue #{DONE} ends with no pull request and no code change. Argus "
    "checked zero commits, a clean tree, and no pull request. The reason is the "
    f"agent's claim, and Argus did not check it: {REASON}. Argus starts no new run for "
    "it. Action: close the issue. The next tick then releases the claim. Check: the "
    "claim label is gone. If the reason is wrong, remove the claim label to retry."
)
NO_REASON_NOTICE = (
    f"Needs You. Issue #{DONE} finished its run with no pull request. Argus starts no "
    "new run for it. Action: remove the claim label to retry."
)
SETUP = """
PYTHON="$TEST_PYTHON"; ARGUS="$TEST_DRIVER/.."; REPO="$TEST_REPO"
RUN_DIR="$TEST_STATE"; LOG="$TEST_STATE/driver.log"
ARGUS_CONFIG_ARGS=(--config "$TEST_CONFIG"); BOARD_KIND_ARGS=(); GITSYNC_KIND_ARGS=()
WORK_SOURCE=issues; WORK_SOURCE_FLAGS=(--source issues)
DRY_RUN=0; PHASE_MACHINE_ID=worker-one; RECOVER_OWN_CLAIM=0; SESSION=fixture
_DRIVE_RC=""; out=""; rc=0; selection_rc=0; number=""
notify() { printf '%s\\n' "$1" >>"$TEST_STATE/notes.txt"; }
notify_coordination_outage() { :; }
phase_run_dir() { printf '%s/runs/issue-%06d' "$TEST_VAULT" "$1"; }
"""
TICK = '_DRIVE_RC=""; number=""; _drive_choose_selection\nprintf "%s" "$number"\n'


@dataclass
class Case:
    root: Path
    vault: Path
    github: Path
    state: Path
    config: Path
    store: ReservationStore

    def run_folder(self) -> Path:
        return self.vault / "runs" / f"issue-{KEY}"

    def github_says(self, state: str, labels: list[dict[str, str]] = HELD) -> None:
        done = issue(DONE, state=state, labels=labels)
        waiting = issue(NEXT, labels=FREE, createdAt="2025-01-01T12:00:00Z")
        fixture_update(
            self.github,
            rows=[done, waiting] if state == "OPEN" else [waiting],
            views={str(DONE): done, str(NEXT): waiting},
        )

    def shell(self, body: str) -> str:
        sources = "\n".join(f'. "$TEST_DRIVER/{name}"' for name in LIBS)
        done = subprocess.run(
            ["bash", "-u", "-c", sources + SETUP + body],
            cwd=self.root,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", str(self.state)),
                "TEST_DRIVER": str(DRIVER),
                "TEST_REPO": str(self.root),
                "TEST_STATE": str(self.state),
                "TEST_CONFIG": str(self.config),
                "TEST_VAULT": str(self.vault),
                "TEST_PYTHON": sys.executable,
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    def tick(self) -> str:
        """One real selection; return the issue number this tick would launch."""
        return self.shell(TICK)

    def hold(self) -> None:
        """The real hold with the verdict read, for a state the selector never gives."""
        self.shell(
            "stub_verdict() { printf none; }\nISSUE_PR_VERDICT_CMD=stub_verdict\n"
            f"issue_completed_hold {DONE} tasks/{TASK}\n"
        )

    def notes(self) -> list[str]:
        path = self.state / "notes.txt"
        return path.read_text("utf-8").splitlines() if path.exists() else []

    def closes(self) -> list[list[str]]:
        if not (self.github / "argv.jsonl").exists():
            return []
        return [
            line["argv"]
            for line in argv(self.github)
            if line["argv"][:2] == ["issue", "close"]
        ]

    def board(self) -> dict[str, Any]:
        return self.store.read()[0]["phases"]

    def log(self) -> str:
        return (self.state / "driver.log").read_text("utf-8")


@pytest.fixture
def case(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Case]:
    """A claimed issue whose run ended no-change, first in the queue, with one behind."""
    previous = conventions.override({"commands.git": "git"})
    _remote, root = _init_remote(tmp_path, "public")
    _private, seed = _init_remote(tmp_path, "private")
    vault = root / "vault"
    shutil.move(str(seed), vault)
    exclude = root / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "vault/\n", encoding="utf-8")
    github = tmp_path / "github"
    github.mkdir()
    binary = fake_gh(github, [], add_cleanup=request.addfinalizer)
    (tmp_path / "machine.id").write_text("worker-one\n", encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("inputs: {}\n", encoding="utf-8")
    document = copy.deepcopy(BASE)
    document.pop("phases")
    document.update(
        {
            "repo-root": str(root),
            "pipeline": str(pipeline),
            "number-input": "issue_number",
            "file-input": "issue_file",
            "machine": {"id-file": str(tmp_path / "machine.id")},
            "board": {
                "repo": str(vault),
                "file": "phases.json",
                "issues-file": "issues.json",
                "branch": "fixture-claims",
                "remote": "origin",
            },
            "issues": {
                "repo": "example/project",
                "preference": "newest",
                "label-groups": GROUPS,
                "difficulty-order": ["difficulty:easy", "difficulty:hard"],
                "task-dir": str(vault / "tasks"),
                "claim-label": "accepted",
            },
        }
    )
    document["driver"].update(
        {"provider": "issues", "runs": str(vault / "runs"), "state": ".issue-driver"}
    )
    document["commands"].update({"gh": str(binary), "python": sys.executable})
    config = tmp_path / "config.json"
    config.write_text(json.dumps(document), encoding="utf-8")
    conventions.configure_from_cli(["--config", str(config)])
    store = ReservationStore(root, issues=True)
    store.migrate_board()
    token = store.reserve(DONE, "worker-one", run_id=f"issue-{KEY}")
    task = vault / "tasks" / TASK
    task.parent.mkdir(parents=True)
    task.write_text("frozen task\n", encoding="utf-8")
    save(root, DONE, token, task)
    subprocess.run(["git", "-C", str(root), "checkout", "-qb", BRANCH], check=True)
    state = tmp_path / "state"
    state.mkdir()
    made = Case(root, vault, github, state, config, store)
    run = made.run_folder()
    (run / "run.json").write_text(json.dumps({"state": "completed"}), "utf-8")
    (run / "no-change.txt").write_text(f"{REASON}\n", "utf-8")
    made.github_says("OPEN")
    yield made
    conventions.configure(previous)
