# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The stubbed-remote fixtures the self-test builds its cases on."""

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.gitsync.git import git, git_binary

# A fixture path only; production paths come from the loaded config.
BOARD_REL = Path("vault") / "notes"


def _check(
    checks: list[dict[str, Any]], name: str, ok: object, detail: str = ""
) -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _init_remote(tmp: Path, name: str) -> tuple[Path, Path]:
    """Bare remote with HEAD -> main, plus a clone at tmp/<name> that has
    one commit on main tracking origin/main.

    The remote is seeded by a throwaway clone *before* the real one is made, and
    that ordering is load-bearing rather than incidental. ``integration_branch``
    resolves a checkout's integration branch by checking for ``master`` and
    otherwise reading ``refs/remotes/origin/HEAD``; a clone of an empty remote
    gets no ``origin/HEAD`` at all, so the resolver falls through to its
    hard-coded default and returns a branch this fixture never created. Every
    check downstream then resolves a branch that does not exist, and fails for a
    reason that has nothing to do with the code under test. Seeding first makes
    the clone carry a correct ``origin/HEAD``, so the resolver finds ``main``
    exactly the way it finds ``master`` in production.
    """
    remote = tmp / f"{name}-remote.git"
    subprocess.run(
        [git_binary(), "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp / f"{name}-seed"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(seed)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(seed, "config", "user.email", "sync-test@example.com")
    git(seed, "config", "user.name", "sync test")
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "a.txt")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "push", "-q", "-u", "origin", "main")
    work = tmp / name
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(work)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(work, "config", "user.email", "sync-test@example.com")
    git(work, "config", "user.name", "sync test")
    git(work, "checkout", "-q", "-b", "main")
    git(work, "push", "-q", "-u", "origin", "main")
    return remote, work


def _remote_commit(remote: Path, tmp: Path, name: str, content: str = "two\n") -> None:
    """Push one commit to the remote from a throwaway clone."""
    other = tmp / f"other-{name}"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(other)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(other, "config", "user.email", "sync-test@example.com")
    git(other, "config", "user.name", "sync test")
    (other / "a.txt").write_text(content)
    git(other, "add", "a.txt")
    git(other, "commit", "-q", "-m", f"remote {name}")
    git(other, "push", "-q", "origin", "main")


def _local_commit(work: Path, name: str) -> None:
    (work / "b.txt").write_text(f"{name}\n")
    git(work, "add", "b.txt")
    git(work, "commit", "-q", "-m", f"local {name}")


def _configure_self_test(
    pub: str | Path,
    board: str | Path,
    *,
    pipeline: str | Path | None = None,
    roadmap: str | Path | None = None,
    runs: str | Path | None = None,
    include: tuple[str, ...] = ("phase-*.md",),
    exclude: tuple[str, ...] = (),
    number_input: str = "phase_number",
    file_input: str = "phase_file",
) -> None:
    pub = Path(pub).resolve()
    board = Path(board).resolve()
    try:
        board_rel = board.relative_to(pub)
    except ValueError:
        board_rel = None
    if board_rel is not None:
        exclude_file = pub / ".git" / "info" / "exclude"
        entry = board_rel.as_posix().rstrip("/") + "/"
        if exclude_file.is_file():
            current = exclude_file.read_text(encoding="utf-8")
            if entry not in current.splitlines():
                exclude_file.write_text(current + entry + "\n", encoding="utf-8")
    conventions.configure(
        {
            "repo-root": pub,
            "pipeline": pipeline,
            "roadmap-dir": Path(roadmap or Path(board) / "roadmap"),
            "driver.runs": Path(runs or Path(board) / "runs"),
            "driver.include": list(include),
            "driver.exclude": list(exclude),
            "number-input": number_input,
            "file-input": file_input,
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": board,
            "board.file": board / "claims.json",
            "board.branch": "main",
            "board.remote": "origin",
        }
    )


class _StubGH:
    """Stand in for ``gh`` so the merge check can be driven by return value.

    The pipeline calls ``gh`` as a subprocess, so the only seam available is the
    process itself. Replacing ``subprocess.run`` for the duration is what lets
    each answer be tested without a token and without a network, and it is
    scoped with ``with`` so a failing assertion cannot leave the stub installed
    for a later check.
    """

    saved: Callable[..., Any]

    def __init__(
        self,
        result: dict[str, Any] | Exception | Callable[[Any], dict[str, Any]],
    ) -> None:
        self.result = result
        self.calls: list[Any] = []

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
        import types

        # Only gh is stubbed. Everything else in this module runs git through
        # the same subprocess.run, so a stub that swallowed those would make
        # every git call under test fail, which is a failure in the harness
        # rather than a finding about the code.
        if not argv or Path(argv[0]).name != "gh":
            return self.saved(argv, **kwargs)
        self.calls.append(argv)
        if isinstance(self.result, Exception):
            raise self.result
        if callable(self.result):
            self.result = self.result(argv)
        return types.SimpleNamespace(
            returncode=self.result.get("returncode", 0),
            stdout=self.result.get("stdout", ""),
            stderr=self.result.get("stderr", ""),
        )

    def __enter__(self) -> "_StubGH":
        self.saved = subprocess.run
        # mypy types run as variadic; the stub matches the call shape used here.
        subprocess.run = cast(Any, self)
        return self

    def __exit__(self, *_exc: object) -> None:
        subprocess.run = self.saved


def _gh_says(state: str, extra: str = "") -> dict[str, str]:
    """A ``gh pr view --json state`` response body."""
    return {
        "stdout": json.dumps(
            {
                "state": state,
                "mergedAt": extra or None,
                "mergeCommit": {"oid": "0" * 40},
            }
        )
    }


def _write_group_phases(roadmap: Path, members: Sequence[int], behaviour: str) -> None:
    """Two phase documents declaring one symmetric group, in a fixture roadmap."""
    declaration = f"`together: {members[0]:06d}, {members[1]:06d}`"
    for number in members:
        (roadmap / f"phase-{number:06d}-{behaviour}.md").write_text(
            f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
            f"### Reservation Group\n\n{declaration}\n",
            encoding="utf-8",
        )


__all__ = [
    "_StubGH",
    "_check",
    "_configure_self_test",
    "_gh_says",
    "_init_remote",
    "_local_commit",
    "_remote_commit",
    "_write_group_phases",
]
