# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Seeding the board branch: an empty board is valid, a deleted state file is not."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.git import _git, git_binary
from argus.board.selftest_fixture import _git_test_repo
from argus.board.state import _empty_state, state_rel
from argus.board.store import ReservationStore

BRANCH = "fixture-board"
BACKUP = f"{BRANCH}--argus-backup"
STATE = "coordination/phases.json"


@pytest.fixture
def git_exe() -> str:
    executable = shutil.which("git")
    assert executable is not None
    return executable


def _configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git: str,
    board_repo: str,
    *,
    backup_branch: str | None = None,
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    settings: dict[str, object] = {
        "repo-root": project,
        "board.repo": tmp_path / board_repo,
        "board.branch": BRANCH,
        "board.remote": "origin",
        "board.file": STATE,
        "commands.git": git,
    }
    if backup_branch is not None:
        settings["board.backup-branch"] = backup_branch
    monkeypatch.setattr(conventions, "_settings", settings)
    return project


def _remove_and_push(client: Path, relative: str) -> None:
    assert _git(client, "rm", "-q", relative)[0] == 0
    assert _git(client, "commit", "-q", "-m", "remove state")[0] == 0
    assert _git(client, "push", "-q", "origin", "HEAD:master")[0] == 0


def _ref_commit(repo: Path, ref: str) -> str | None:
    rc, out, _err = _git(repo, "rev-parse", "--verify", ref)
    return out.strip() if rc == 0 else None


def test_seeding_an_empty_board_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "empty-client-1")
    _git_test_repo(tmp_path, "empty")
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.read()[0] == _empty_state()


def test_seeding_refuses_a_state_file_removed_from_the_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "broken-client-1")
    _remote, clients = _git_test_repo(tmp_path, "broken", initial_state=_empty_state())
    _remove_and_push(clients[0], state_rel().as_posix())
    store = ReservationStore(project)
    with pytest.raises(CoordinationUnavailable, match="would read as broken"):
        store.migrate_board()


def test_seeding_refuses_a_shallow_board_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "shallow-client")
    _git_test_repo(tmp_path, "shallow", initial_state=_empty_state())
    # A local path clone ignores --depth, so the shallow copy needs file://.
    subprocess.run(
        [
            git_binary(),
            "clone",
            "--depth",
            "1",
            "-q",
            (tmp_path / "shallow-remote.git").as_uri(),
            str(tmp_path / "shallow-client"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    store = ReservationStore(project)
    with pytest.raises(CoordinationUnavailable, match="shallow"):
        store.migrate_board()


def test_a_lost_board_branch_is_reseeded_from_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "recover-client-1")
    remote, _clients = _git_test_repo(tmp_path, "recover", initial_state=_empty_state())
    store = ReservationStore(project)
    assert store.migrate_board() is True
    record = store.reserve(100020, "server-01")
    # Every write advances the backup with the board it mirrors.
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") == _ref_commit(
        remote, f"refs/heads/{BRANCH}"
    )
    # Lose the board branch; the backup is now the only durable copy.
    assert _git(remote, "update-ref", "-d", f"refs/heads/{BRANCH}")[0] == 0
    assert _ref_commit(remote, f"refs/heads/{BRANCH}") is None
    # The next migration restores the board from the backup, not from a clone.
    assert store.migrate_board() is True
    state, _commit = store.read()
    assert state["phases"]["100020"]["reservation_id"] == record["reservation_id"]


def test_recovery_prefers_the_backup_over_a_broken_integration_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "recover-broken-client-1")
    remote, clients = _git_test_repo(
        tmp_path, "recover-broken", initial_state=_empty_state()
    )
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.reserve(100021, "server-01")
    # After the board moved, the state file leaves the integration tip. A
    # reseed from that tip alone would refuse; the backup must save it.
    _remove_and_push(clients[0], state_rel().as_posix())
    assert _git(remote, "update-ref", "-d", f"refs/heads/{BRANCH}")[0] == 0
    assert store.migrate_board() is True
    state, _commit = store.read()
    assert "100021" in state["phases"]


def test_a_remote_without_atomic_support_still_takes_a_board_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "noatomic-client-1")
    remote, _clients = _git_test_repo(
        tmp_path, "noatomic", initial_state=_empty_state()
    )
    # A server that does not advertise atomic push refuses any --atomic push.
    # The board write must not depend on it, so the branch still lands and the
    # backup follows on its own.
    assert _git(remote, "config", "receive.advertiseAtomic", "false")[0] == 0
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.reserve(100030, "server-01")
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") == _ref_commit(
        remote, f"refs/heads/{BRANCH}"
    )


def test_a_foreign_branch_at_the_backup_name_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "foreign-client-1")
    remote, clients = _git_test_repo(tmp_path, "foreign")
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.reserve(100031, "server-01")
    # A real branch that happens to hold the backup name is not this board's
    # backup. Force it over the remote ref, then make another board write and
    # require the write to succeed and the foreign commit to stay untouched.
    foreign = clients[0]
    (foreign / "foreign.txt").write_text("not the board\n")
    assert _git(foreign, "add", "-A")[0] == 0
    assert _git(foreign, "commit", "-q", "-m", "foreign branch")[0] == 0
    foreign_commit = _ref_commit(foreign, "HEAD")
    assert (
        _git(
            foreign,
            "push",
            "--force",
            "origin",
            f"HEAD:refs/heads/{BACKUP}",
        )[0]
        == 0
    )
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") == foreign_commit
    record = store.reserve(100032, "server-01")
    state, _commit = store.read()
    assert state["phases"]["100032"]["reservation_id"] == record["reservation_id"]
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") == foreign_commit


def test_a_configured_backup_branch_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(
        tmp_path,
        monkeypatch,
        git_exe,
        "custom-backup-client-1",
        backup_branch="custom-backup",
    )
    remote, _clients = _git_test_repo(tmp_path, "custom-backup")
    store = ReservationStore(project)
    assert store.migrate_board() is True
    assert store.reserve(100033, "server-01")
    assert _ref_commit(remote, "refs/heads/custom-backup") == _ref_commit(
        remote, f"refs/heads/{BRANCH}"
    )
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") is None


def test_an_existing_board_branch_gains_a_backup_on_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_exe: str
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "backfill-client-1")
    remote, clients = _git_test_repo(tmp_path, "backfill", initial_state=_empty_state())
    # A board branch created before the backup existed: present on the remote
    # with its state file, and with no backup ref yet.
    assert _git(clients[0], "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")[0] == 0
    assert _ref_commit(remote, f"refs/heads/{BRANCH}") is not None
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") is None
    store = ReservationStore(project)
    assert store.migrate_board() is False
    assert _ref_commit(remote, f"refs/heads/{BACKUP}") == _ref_commit(
        remote, f"refs/heads/{BRANCH}"
    )


def test_a_backup_push_timeout_still_takes_the_board_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_exe: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _configure(tmp_path, monkeypatch, git_exe, "timeout-client-1")
    _git_test_repo(tmp_path, "timeout", initial_state=_empty_state())
    store = ReservationStore(project)
    assert store.migrate_board() is True

    # Every advance of the backup ref goes through ``_git``, which raises
    # ``CoordinationUnavailable`` on a timeout rather than returning a failure
    # code. The board write the backup mirrors has already succeeded, so the
    # timeout must be reported, not raised.
    def timeout_backup_push(
        repo: str | Path, *args: str, timeout: int | None = None
    ) -> tuple[int, str, str]:
        if args[:1] == ("push",) and any(BACKUP in arg for arg in args):
            raise CoordinationUnavailable("git push timed out after 180s")
        if timeout is None:
            return _git(repo, *args)
        return _git(repo, *args, timeout=timeout)

    # Patch where ``branch`` looks the name up, so only the backup push fails;
    # every other git call still reaches the real runner.
    monkeypatch.setattr("argus.board.branch._git", timeout_backup_push)
    record = store.reserve(100040, "server-01")
    assert record["reservation_id"]
    state, _commit = store.read()
    assert state["phases"]["100040"]["reservation_id"] == record["reservation_id"]
    assert "coordination backup ref" in capsys.readouterr().err


def test_cli_module_runs_and_reports_a_missing_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "argus.board.cli"],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "a command is required" in result.stderr


def _module_prog() -> str:
    # argparse derives the default prog from how the interpreter found the
    # __main__ code. Before Python 3.14 that is the file name; from 3.14 on it is
    # "<python> -m <module>". See https://docs.python.org/3.14/whatsnew/3.14.html#argparse
    if sys.version_info >= (3, 14):
        return f"{Path(sys.executable).name} -m argus.board.cli"
    return "cli.py"


def test_cli_module_serves_the_show_subcommand(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "argus.board.cli", "show", "--help"],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.startswith(f"usage: {_module_prog()} show ")
    assert "-h, --help" in result.stdout
