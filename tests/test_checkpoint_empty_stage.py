# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A checkpoint with nothing staged pushes what the branch has, and never runs a commit."""

from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import mode_clean
from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.mode_clean import mode_checkpoint
from argus.gitsync.selftest_fixture import BOARD_REL, _configure_self_test, _init_remote

BRANCH = "feature/group-cache-warm"
# What the pre-commit hooks printed on the failed attempts in the incident.
HOOK_REFUSAL = (
    "precommit: no staged file(s) to check, so every hook below had nothing to read."
)


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The self-test configures the git command before it makes a remote; restored after each test.
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


def _phase_clone(tmp_path: Path) -> Path:
    base = tmp_path / "checkpoint"
    base.mkdir()
    pub: Path = _init_remote(base, "checkpoint")[1]
    _configure_self_test(pub, pub / BOARD_REL)
    assert ensure_phase_branch(pub, BRANCH)["ok"]
    return pub


def _spy_git(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, tuple[int, str, str]]
) -> list[tuple[str, ...]]:
    """Run git for real, except the subcommands in ``answers``; every call is recorded."""
    real_git = mode_clean.git
    calls: list[tuple[str, ...]] = []

    def spy(root: str | Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        if args and args[0] in answers:
            return answers[args[0]]
        return real_git(root, *args)

    monkeypatch.setattr(mode_clean, "git", spy)
    return calls


def test_nothing_staged_pushes_without_a_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub = _phase_clone(tmp_path)
    # The hook refuses an empty commit. The fix must never let one reach it.
    calls = _spy_git(monkeypatch, {"commit": (1, "", HOOK_REFUSAL)})
    result = mode_checkpoint(pub)
    assert result["ok"] is True
    assert result["action"] == "nothing-to-push"
    assert not [c for c in calls if c[:1] == ("commit",)]


def test_staged_work_is_committed_and_pushed(tmp_path: Path) -> None:
    pub = _phase_clone(tmp_path)
    (pub / "unfinished.txt").write_text("half a phase\n", encoding="utf-8")
    result = mode_checkpoint(pub)
    assert result["ok"] is True
    assert result["action"] == "committed-and-pushed"


def test_a_branch_already_carrying_the_commit_is_reported_as_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The repair pipeline commits on the branch before the checkpoint runs, so
    # the stage is empty while the push still moves the branch.
    pub = _phase_clone(tmp_path)
    (pub / "fix.txt").write_text("the fix\n", encoding="utf-8")
    assert mode_clean.git(pub, "add", "fix.txt")[0] == 0
    assert mode_clean.git(pub, "commit", "-qm", "the repair's commit")[0] == 0
    calls = _spy_git(monkeypatch, {"commit": (1, "", HOOK_REFUSAL)})
    result = mode_checkpoint(pub)
    assert result["ok"] is True
    assert result["action"] == "pushed"
    assert not [c for c in calls if c[:1] == ("commit",)]


def test_an_unreadable_stage_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub = _phase_clone(tmp_path)
    calls = _spy_git(monkeypatch, {"diff": (128, "", "fatal: index is locked")})
    result = mode_checkpoint(pub)
    assert result["ok"] is True
    assert "could not read the staged changes" in result["warning"]
    assert not [c for c in calls if c[:1] in (("commit",), ("push",))]


def test_scoped_checkpoint_leaves_other_work_uncommitted(tmp_path: Path) -> None:
    pub = _phase_clone(tmp_path)
    (pub / "selected.txt").write_text("work\n")
    (pub / "other.txt").write_text("leave me\n")
    result = mode_checkpoint(pub, ["selected.txt"])
    assert result["ok"] is True
    assert result["left_uncommitted"] == ["other.txt"]
    assert mode_clean.git(pub, "status", "--porcelain")[1] == "?? other.txt"
    assert mode_clean.git(pub, "log", "-1", "--format=%s")[1].startswith("wip:")


def test_scoped_checkpoint_reports_other_staged_work(tmp_path: Path) -> None:
    pub = _phase_clone(tmp_path)
    (pub / "selected.txt").write_text("work\n")
    (pub / "other.txt").write_text("leave me\n")
    assert mode_clean.git(pub, "add", "other.txt")[0] == 0
    before = mode_clean.git(pub, "status", "--porcelain")[1]
    result = mode_checkpoint(pub, ["selected.txt"])
    assert result["ok"] is True
    assert "outside checkpoint paths" in result["warning"]
    assert mode_clean.git(pub, "status", "--porcelain")[1] == before


def test_scoped_checkpoint_reports_rename_from_outside_scope(tmp_path: Path) -> None:
    pub = _phase_clone(tmp_path)
    assert mode_clean.git(pub, "mv", "a.txt", "selected.txt")[0] == 0
    before = mode_clean.git(pub, "status", "--porcelain")[1]
    result = mode_checkpoint(pub, ["selected.txt"])
    assert result["ok"] is True
    assert "outside checkpoint paths" in result["warning"]
    assert mode_clean.git(pub, "status", "--porcelain")[1] == before


def test_root_scope_checkpoint_skips_an_ignored_tree(tmp_path: Path) -> None:
    # The boundary stages the whole root while an ignored private tree exists.
    # A negative pathspec would make ``git add`` refuse that tree instead of
    # skipping it; the root spec must leave it alone and commit the rest.
    pub = _phase_clone(tmp_path)
    (pub / ".git" / "info" / "exclude").write_text("private/\n", encoding="utf-8")
    ignored = pub / "private" / "host-checkout" / "runs"
    ignored.mkdir(parents=True)
    (ignored / "state.json").write_text("{}\n", encoding="utf-8")
    (pub / "wanted.txt").write_text("work\n", encoding="utf-8")
    result = mode_checkpoint(pub, ["."])
    assert result["ok"] is True
    assert result["action"] == "committed-and-pushed"
    assert mode_clean.git(pub, "log", "-1", "--format=%s")[1].startswith("wip:")
    assert mode_clean.git(pub, "status", "--porcelain")[1] == ""
