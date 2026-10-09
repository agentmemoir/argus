# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue branch names, frozen task scopes, and publication fences."""

import re
from pathlib import Path
from typing import Any

from argus import conventions
from argus.board.core import CoordinationError, _issue_key
from argus.board.files import load_reservation_file, reservation_file
from argus.gitsync.git import ConfigError, git, is_git_repo
from argus.issues.claims import snapshot
from argus.issues.task import task_directory
from argus.issues.transport import IssuesUnavailable


def issue_number(value: object) -> int:
    try:
        return _issue_key(value)[1]
    except CoordinationError as exc:
        raise ConfigError(str(exc)) from exc


def issue_branch(number: int | str, task_file: str | Path | None) -> str:
    number = issue_number(number)
    match = re.fullmatch(
        r"issue-([0-9]+)(?:-([a-z0-9]+(?:-[a-z0-9]+)*))?",
        Path(str(task_file or "")).stem,
    )
    if match is None or int(match.group(1)) != number:
        raise ConfigError("issue task filename does not match the issue number")
    return f"issue/{number}-{match.group(2) or 'task'}"


def private_layout(root: str | Path) -> tuple[Path, Path]:
    root = Path(root).resolve()
    private = conventions.board_repo(root)
    if private is None or private == root or not is_git_repo(private):
        raise ConfigError("issue publication needs a separate private checkout")
    try:
        tasks = task_directory(root)
    except IssuesUnavailable as exc:
        raise ConfigError(str(exc)) from exc
    runs = conventions.runs_dir(root).resolve()
    if not tasks.is_relative_to(private) or tasks == private:
        raise ConfigError(
            "issues.task-dir must be inside the private checkout to publish"
        )
    if not runs.is_relative_to(private) or runs == private:
        raise ConfigError(
            "issue run directory must be inside the private checkout to publish"
        )
    # A nested checkout must not also be tracked by the public repository.
    if private.is_relative_to(root):
        rc, tracked, _err = git(
            root, "ls-files", "--", private.relative_to(root).as_posix()
        )
        if rc != 0 or tracked:
            raise ConfigError("public checkout tracks private issue data")
    return private, tasks


def load_issue_reservation(
    root: str | Path,
    number: int | str,
    *,
    path: str | Path | None = None,
    machine_id: str | None = None,
    reservation_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    number = issue_number(number)
    expected = reservation_file(root, number, kind="issue").resolve()
    if path is not None and Path(path).resolve() != expected:
        raise ConfigError("issue reservation file must be inside the current run")
    data = load_reservation_file(expected)
    if data is None or "phase" in data or issue_number(data.get("issue")) != number:
        raise ConfigError("reservation does not belong to the current issue")
    if machine_id is not None:
        data["machine_id"] = machine_id
    if reservation_id is not None:
        data["reservation_id"] = reservation_id
    if generation is not None:
        data["generation"] = generation
    return data


def issue_scope(root: str | Path, number: int | str) -> tuple[Path, list[str]]:
    root = Path(root).resolve()
    number = issue_number(number)
    private, tasks = private_layout(root)
    run = reservation_file(root, number, kind="issue").resolve().parent
    token = load_issue_reservation(root, number)
    task = snapshot(root, number, token)
    if task is None or not task.is_relative_to(tasks):
        raise ConfigError("current issue has no valid frozen task snapshot")
    issue_branch(number, task)
    if not run.is_relative_to(private) or not task.is_relative_to(private):
        raise ConfigError("issue data must be inside the private checkout")
    return private, [
        run.relative_to(private).as_posix(),
        task.relative_to(private).as_posix(),
    ]


def inside_scope(path: str, scopes: list[str]) -> bool:
    return path == scopes[1] or path == scopes[0] or path.startswith(scopes[0] + "/")
