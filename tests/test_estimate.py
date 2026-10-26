# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The estimate factor update a finished phase makes.

The repositories here are plain git directories under `tmp_path`. Nothing is
executed from them: the ideal-days command is the running Python with `-c`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from argus import conventions, estimate

HARNESS = "cmd:model-a"


def test_factor_moves_at_most_twenty_percent_up() -> None:
    assert estimate.bounded_factor(1.0, 1.39) == pytest.approx(1.2)


def test_factor_moves_at_most_twenty_percent_down() -> None:
    assert estimate.bounded_factor(1.5, 0.5) == pytest.approx(1.2)


def test_factor_takes_the_observed_value_inside_the_band() -> None:
    assert estimate.bounded_factor(1.2, 1.33) == pytest.approx(1.33)


def test_observed_factor_is_hours_over_two_per_ideal_day() -> None:
    # 101041: 7h40 of wall time against 2.75 ideal days.
    assert estimate.observed_factor(460 * 60, 2.75) == pytest.approx(1.394, abs=1e-3)


def test_new_model_starts_at_the_slowest_known_factor() -> None:
    assert estimate.start_factor({"a": 1.0, "b": 1.6}, "new") == 1.6
    assert estimate.start_factor({"a": 1.0, "b": 1.6}, "a") == 1.0


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
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(remote))
    repo = tmp_path / "private"
    git(tmp_path, "clone", "-q", str(remote), str(repo))
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    factors = repo / "factors.json"
    factors.write_text(json.dumps({"factors": {HARNESS: 1.0}}), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "push", "-q", "origin", "master")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    steps = {"steps": {"developer": {"harness": HARNESS}}}
    (run_dir / "harnesses.json").write_text(json.dumps(steps), encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} -c 'print(2.75)'"
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "commands.git": "git",
            "repo-root": str(tmp_path),
            "estimate.factors-file": str(factors),
            "estimate.ideal-days": command,
        },
    )
    return {"repo": repo, "factors": factors, "run_dir": run_dir, "remote": remote}


def factor_in(path: Path) -> float:
    return json.loads(path.read_text(encoding="utf-8"))["factors"][HARNESS]


def test_finished_phase_updates_commits_and_pushes_the_factor(
    setup: dict[str, Path],
) -> None:
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=False)
    assert "1 -> 1.2" in line
    assert factor_in(setup["factors"]) == pytest.approx(1.2)
    assert git(setup["repo"], "status", "--porcelain") == ""
    pushed = git(setup["remote"], "log", "-1", "--format=%s", "master")
    assert "101041" in pushed


def test_resumed_run_leaves_the_factor_alone(setup: dict[str, Path]) -> None:
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=True)
    assert "resumed" in line
    assert factor_in(setup["factors"]) == 1.0


def test_harness_only_edit_changes_the_factor_key(setup: dict[str, Path]) -> None:
    # A harness-only edit carries no matching index, display, or options; the
    # saved `harness` string is still what the factor is keyed on. A map this
    # reader cannot use is a skipped update, never a stop.
    run_dir = setup["run_dir"]
    (run_dir / "harnesses.json").write_text(
        json.dumps({"steps": {"developer": {"harness": "cmd:model-b", "index": 9}}}),
        encoding="utf-8",
    )
    line = estimate.update(101041, run_dir, 460 * 60, resumed=False)
    assert "cmd:model-b" in line
    factors = json.loads(setup["factors"].read_text(encoding="utf-8"))["factors"]
    assert factors["cmd:model-b"] == pytest.approx(1.2)
    assert factors[HARNESS] == 1.0


def test_unreadable_harness_map_continues_without_a_stop(
    setup: dict[str, Path],
) -> None:
    (setup["run_dir"] / "harnesses.json").write_text("not json", encoding="utf-8")
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=False)
    assert "no developer harness recorded" in line
    assert factor_in(setup["factors"]) == 1.0


def test_phase_without_an_estimate_leaves_the_factor_alone(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    monkeypatch.setitem(conventions._settings, "estimate.ideal-days", failing)  # pyright: ignore[reportPrivateUsage]
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=False)
    assert "no ideal days" in line
    assert factor_in(setup["factors"]) == 1.0


def test_failed_commit_puts_the_file_back(setup: dict[str, Path]) -> None:
    git(setup["repo"], "remote", "remove", "origin")
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=False)
    assert "not committed" in line
    assert factor_in(setup["factors"]) == 1.0
    assert git(setup["repo"], "status", "--porcelain") == ""


def test_unconfigured_driver_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conventions, "_settings", {})
    assert estimate.update(1, Path("/nonexistent"), 60, resumed=False) == ""


def test_push_conflict_reverts_the_local_commit(
    setup: dict[str, Path], tmp_path: Path
) -> None:
    # Another machine pushed its own update of the same factor first.
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(setup["remote"]), str(other))
    git(other, "config", "user.email", "o@example.invalid")
    git(other, "config", "user.name", "o")
    (other / "factors.json").write_text(json.dumps({"factors": {HARNESS: 0.9}}))
    git(other, "commit", "-q", "-am", "other machine")
    git(other, "push", "-q", "origin", "master")
    line = estimate.update(101041, setup["run_dir"], 460 * 60, resumed=False)
    assert "reverted" in line
    git(setup["repo"], "fetch", "-q", "origin")
    git(setup["repo"], "merge", "-q", "--no-edit", "origin/master")
    assert factor_in(setup["factors"]) == pytest.approx(0.9)
