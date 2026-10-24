# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""base-sync: no conflict changes nothing, a clean merge is one merge commit and
one push, a text conflict leaves the merge for an agent, and a failed read or a
hostile branch name is a failure, never "no conflict"."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import base_sync as bs
from argus.gitsync import base_sync_cli
from config_fixture import write as write_config
from config_issue_fixture import issue_document

BRANCH = "issue/248-driver-checks-for"


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _identity(repo: Path) -> None:
    # A CI host has no global git identity, and base-sync's merge commit needs one.
    _git(repo, "config", "user.name", "argus-test")
    _git(repo, "config", "user.email", "argus-test@example.com")


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "--quiet", "-m", f"change {name}")


def _move_base(repos: dict[str, Path], name: str, text: str) -> None:
    _commit(repos["seed"], name, text)
    _git(repos["seed"], "push", "--quiet", str(repos["origin"]), "master")


@pytest.fixture
def repos(tmp_path: Path) -> dict[str, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "master", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--quiet", "-b", "master", str(seed))
    _identity(seed)
    _commit(seed, "app.txt", "one\n")
    _git(seed, "push", "--quiet", str(origin), "master")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "--quiet", str(origin), str(work))
    _identity(work)
    _git(work, "checkout", "--quiet", "-b", BRANCH)
    _commit(work, "feature.txt", "feature\n")
    _git(work, "push", "--quiet", "-u", "origin", BRANCH)
    return {"origin": origin, "seed": seed, "work": work}


def test_branch_that_has_the_base_changes_nothing(repos: dict[str, Path]) -> None:
    before = _git(repos["work"], "rev-parse", "HEAD")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.NO_CONFLICT
    assert _git(repos["work"], "rev-parse", "HEAD") == before
    assert bs.report_lines(out) == ["base-sync: no conflict with master"]


def test_clean_base_change_is_one_merge_commit_and_one_push(
    repos: dict[str, Path],
) -> None:
    _move_base(repos, "other.txt", "base side\n")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.MERGED
    head = _git(repos["work"], "rev-parse", "HEAD")
    assert out["head"] == head
    # The merge commit and its two parents.
    parents = _git(repos["work"], "rev-list", "--parents", "-n", "1", "HEAD")
    assert len(parents.split()) == 3
    assert _git(repos["origin"], "rev-parse", f"refs/heads/{BRANCH}") == head


def test_text_conflict_leaves_the_merge_for_an_agent(repos: dict[str, Path]) -> None:
    # Both sides add feature.txt with different text: an add/add conflict.
    _move_base(repos, "feature.txt", "base version\n")
    pushed = _git(repos["origin"], "rev-parse", f"refs/heads/{BRANCH}")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.TEXT_CONFLICT
    assert out["conflicted"] == ["feature.txt"]
    assert bs.EXIT_CODES[out["state"]] == 3
    assert _git(repos["work"], "rev-parse", "-q", "--verify", "MERGE_HEAD")
    assert _git(repos["origin"], "rev-parse", f"refs/heads/{BRANCH}") == pushed


def test_failed_fetch_is_a_failure_not_no_conflict(repos: dict[str, Path]) -> None:
    _git(repos["work"], "remote", "set-url", "origin", str(repos["work"] / "gone"))
    before = _git(repos["work"], "rev-parse", "HEAD")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.FAILED
    assert bs.EXIT_CODES[out["state"]] == 1
    assert bs.report_lines(out) == ["base-sync: failed: git fetch origin failed"]
    assert _git(repos["work"], "rev-parse", "HEAD") == before


def test_unreadable_base_is_repaired_once_then_fails_closed(
    repos: dict[str, Path],
) -> None:
    _git(repos["work"], "remote", "set-url", "origin", str(repos["work"] / "gone"))
    _git(repos["work"], "remote", "set-head", "origin", "-d")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.FAILED
    assert "origin/HEAD" in out["reason"]


def test_checkout_on_another_branch_is_refused(repos: dict[str, Path]) -> None:
    _git(repos["work"], "checkout", "--quiet", "master")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.FAILED
    assert "the checkout is on 'master'" in out["reason"]


def test_detached_checkout_at_the_remote_tip_is_accepted(
    repos: dict[str, Path],
) -> None:
    _git(repos["work"], "checkout", "--quiet", "--detach", f"origin/{BRANCH}")
    _move_base(repos, "other.txt", "base side\n")
    out = bs.base_sync(repos["work"], BRANCH)
    assert out["state"] == bs.MERGED
    head = _git(repos["work"], "rev-parse", "HEAD")
    assert _git(repos["origin"], "rev-parse", f"refs/heads/{BRANCH}") == head


@pytest.mark.parametrize(
    "name", ["feature;touch MARKER", "$(touch MARKER)", "-f", "x y", ""]
)
def test_hostile_branch_name_is_data_and_fails(
    repos: dict[str, Path], tmp_path: Path, name: str
) -> None:
    before = _git(repos["work"], "rev-parse", "HEAD")
    out = bs.base_sync(repos["work"], name)
    assert out["state"] == bs.FAILED
    assert _git(repos["work"], "rev-parse", "HEAD") == before
    assert not (repos["work"] / "MARKER").exists()
    assert not (tmp_path / "MARKER").exists()


def test_cli_prints_one_line_and_maps_the_exit_code(
    repos: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code = base_sync_cli.main(["--repo", str(repos["work"]), "--pr-branch", BRANCH])
    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "base-sync: no conflict with master"
    ]
    _move_base(repos, "feature.txt", "base version\n")
    code = base_sync_cli.main(["--repo", str(repos["work"]), "--pr-branch", BRANCH])
    assert code == 3
    assert "base-sync: conflicted path feature.txt" in capsys.readouterr().out


def test_config_check_ignores_the_checkout_flag(tmp_path: Path) -> None:
    # The driver passes the repair worktree as --repo, and runs from it. The config
    # reader must not take --repo for --repo-root by prefix.
    config = write_config(tmp_path / "root", json.dumps(issue_document()))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    script = Path(__file__).resolve().parents[1] / "src/argus/gitsync/base_sync_cli.py"
    done = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            str(config),
            "--repo",
            str(checkout),
            "--pr-branch",
            BRANCH,
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "configuration error" not in done.stdout
    assert done.stdout.startswith("base-sync: failed:")
    assert done.returncode == 1
