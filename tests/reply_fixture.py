# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Rows, snapshots, and a fake ``gh`` shared by the pull request reply tests."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import pr_signal_reply
from argus.gitsync.pr_signal_store import SNAPSHOT_FILE

REF = {"host": None, "owner": "example-org", "repo": "example-repo", "number": 7}


def comment(_cid: int, in_reply_to: int | None = None, body: str = "Fix it.") -> Any:
    return {
        "author": "reviewer",
        "path": "src/a.rs",
        "line": 9,
        "in_reply_to": in_reply_to,
        "updated_at": "t",
        "reactions": 0,
        "trusted": True,
        "body": body,
    }


def snapshot(comments: dict[str, Any]) -> dict[str, Any]:
    return {"alerts": {}, "comments": comments, "pull_request": REF}


def record(run_dir: Path, comments: dict[str, Any]) -> None:
    (run_dir / SNAPSHOT_FILE).write_text(json.dumps(snapshot(comments)))


def fresh(comments: dict[str, Any]) -> Any:
    def read(*_args: object) -> Any:
        return snapshot(comments), ""

    return read


def no_read(*_args: object) -> Any:
    return None, "gh did not answer within 60s"


def spy_gh(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
    posts: list[list[str]] = []

    def fake_gh(argv: list[str], *_args: object) -> Any:
        posts.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout=""), ""

    monkeypatch.setattr(pr_signal_reply, "_gh", fake_gh)
    return posts
