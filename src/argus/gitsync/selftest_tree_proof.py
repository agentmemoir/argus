# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tree-proof check: a phase's content found on the integration branch."""

from pathlib import Path

from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import git, integration_branch
from argus.gitsync.paths import phase_branch_for
from argus.gitsync.receipts import _head_carried_locally, head_tree_on_integration
from argus.gitsync.selftest_publish import _publish_fixture


def _tree_proof_checks(base: Path) -> list[tuple[str, object, str]]:
    """The tree a phase published must be findable on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see the merge. What survives is the content: the squash commit's
    tree is the phase branch's tree. That is the proof this checks, and it is
    the one that still works when GitHub cannot be asked at all.
    """
    results: list[tuple[str, object, str]] = []
    pub, _nested, _run = _publish_fixture(base, "tree-proof", 100482)
    integration: str = integration_branch(pub)
    branch = phase_branch_for(100482, "phase-100482-tree-proof.md")
    ensure_phase_branch(pub, branch, base=integration)
    for step in range(3):
        (pub / f"part-{step}.txt").write_text(f"work {step}\n")
        git(pub, "add", f"part-{step}.txt")
        git(pub, "commit", "-q", "-m", f"phase 100482 step {step}")
    head: str = git(pub, "rev-parse", "HEAD")[1]
    git(pub, "checkout", "-q", integration)
    git(pub, "merge", "-q", "--squash", branch)
    git(pub, "commit", "-q", "-m", "phase 100482 (squashed)")
    results.append(
        (
            "a squash-merged phase's content is on the integration branch even "
            "though its commit is not",
            _head_carried_locally(pub, head, (integration,)) is None
            and head_tree_on_integration(pub, head, integration),
            f"head={head[:12]}",
        )
    )

    # The negative direction: a tree that was never merged must not be found.
    other = phase_branch_for(100483, "phase-100483-unmerged.md")
    ensure_phase_branch(pub, other, base=integration)
    (pub / "unmerged.txt").write_text("still open\n")
    git(pub, "add", "unmerged.txt")
    git(pub, "commit", "-q", "-m", "phase 100483: not merged")
    unmerged_head: str = git(pub, "rev-parse", "HEAD")[1]
    git(pub, "checkout", "-q", integration)
    results.append(
        (
            "content that was never merged is not found on the integration branch",
            not head_tree_on_integration(pub, unmerged_head, integration),
            f"head={unmerged_head[:12]}",
        )
    )
    return results


__all__ = ["_tree_proof_checks"]
