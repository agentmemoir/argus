# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Two driver configs on one repository root share one checkout (issue 199).

Each config takes its own lock, in its own run folder, so the two ticks do not
wait for each other. The branch cut does not look at the lock. It reads only the
checkout: a clean checkout is moved to the next phase's branch. This test runs
the real branch cut for both configs on one root, with a real git repository and
no network. It covers the branch switch only. The clean and checkpoint paths are
shell code in the driver and are not run here.
"""

from pathlib import Path

import pytest

from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import current_branch
from driver_fence_fixture import git, make_repo


@pytest.fixture
def shared_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One checkout on the integration branch, with a local remote for it."""
    root = make_repo(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(root, "branch", "-M", "master")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "master")
    return root


def test_second_config_cut_moves_a_clean_checkout_off_the_first_phase(
    shared_root: Path,
) -> None:
    # Driver A cuts its phase branch, and its agent commits work on it.
    assert ensure_phase_branch(shared_root, "phase-a")["ok"]
    (shared_root / "agent-a.txt").write_text("work from driver A\n")
    git(shared_root, "add", "-A")
    git(shared_root, "commit", "-qm", "agent A work")

    # Driver B, with its own config and run folder, cuts its own phase branch
    # on the same root while A's agent is still running.
    cut_b = ensure_phase_branch(shared_root, "phase-b")

    assert cut_b["ok"], cut_b
    assert current_branch(shared_root) == "phase-b", "driver B did not switch"


def test_second_config_cut_refuses_a_dirty_checkout(shared_root: Path) -> None:
    # Driver A's agent has edited a file and has not committed it yet.
    assert ensure_phase_branch(shared_root, "phase-a")["ok"]
    (shared_root / "agent-a.txt").write_text("uncommitted work from driver A\n")

    cut_b = ensure_phase_branch(shared_root, "phase-b")

    assert not cut_b["ok"], cut_b
    assert current_branch(shared_root) == "phase-a"
    assert (shared_root / "agent-a.txt").read_text() == (
        "uncommitted work from driver A\n"
    )
