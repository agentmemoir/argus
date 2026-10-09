# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The branch-pair and merge-gate checks."""

from pathlib import Path

from argus import conventions
from argus.gitsync_branch import branch_phase_checkouts, ensure_phase_branch
from argus.gitsync_git import (
    INTEGRATION_BRANCH,
    current_branch,
    dirty_paths,
    git,
    integration_branch,
)
from argus.gitsync_paths import phase_branch_for
from argus.gitsync_publish import mark_phase_published
from argus.gitsync_publish_phase import publish_phase
from argus.gitsync_receipts import phase_receipt
from argus.gitsync_review import awaiting_review
from argus.gitsync_selftest_publish import _publish_fixture
from argus.gitsync_tidy import return_to_integration


def _branch_pair_checks(base):
    """The public checkout gets the phase branch; the private one does not.

    Checks 1 to 8 above drive ``ensure_phase_branch`` one checkout at a time,
    so every one of them passes whichever checkout it is handed. None of them
    can see the decision this group covers: which of the two real checkouts is
    the one that gets branched. That decision was once written against the
    public path instead of the private one, and because both checkouts report
    ``ok`` either way, every single-repo check stayed green while a phase was
    launched onto the integration branch to write unreviewed code into it.
    """
    results = []
    pub, nested, _ = _publish_fixture(base, "branch-pair", 100809)
    # The shared fixture builds its throwaway pair on "main". Both real
    # checkouts are on "master", and that is the name the phase branch is cut
    # from, so the pair is put on "master" here too. Otherwise this group would
    # pass or fail over a branch name the pipeline never meets in production.
    for repo in (pub, nested):
        git(repo, "checkout", "-q", "-B", INTEGRATION_BRANCH)
        git(repo, "push", "-q", "-u", "origin", INTEGRATION_BRANCH)
    name = "phase-100809-backup-prune-retention.md"
    branch = phase_branch_for(100809, name)
    made = branch_phase_checkouts(pub, branch)
    by_repo = {Path(r["repo"]).resolve(): r for r in made}

    # The public checkout holds the product code, so it is the one that moves.
    public = by_repo[pub.resolve()]
    results.append(
        (
            "the public checkout is put on the phase branch",
            public["ok"]
            and current_branch(pub) == branch
            and git(pub, "rev-parse", "--verify", f"refs/heads/{branch}")[0] == 0,
            f"branch={current_branch(pub)} {public.get('action')}",
        )
    )

    # The private checkout holds run state, so it stays on the integration
    # branch and gets no phase branch of its own. A branch created here would
    # strand run state on a ref nobody merges.
    private = by_repo[nested.resolve()]
    results.append(
        (
            "the private checkout stays on the integration branch and "
            "no phase branch is created for it",
            private["ok"]
            and private["action"] == "stays-on-integration-branch"
            and current_branch(nested) == INTEGRATION_BRANCH
            and git(nested, "rev-parse", "--verify", f"refs/heads/{branch}")[0] != 0,
            f"branch={current_branch(nested)} {private.get('action')}",
        )
    )

    # The two must not be swapped. Assert the paths as well as the branches, so
    # a future swap cannot pass by leaving both checkouts on the same branch.
    results.append(
        (
            "the two checkouts are branched by role, not by accident",
            len(made) == 2
            and Path(made[0]["repo"]).resolve() == pub.resolve()
            and made[0]["action"] == "created-phase-branch"
            and made[1]["action"] == "stays-on-integration-branch",
            f"public={made[0].get('action')} private={made[1].get('action')}",
        )
    )

    # The receipt is what the driver logs, so it has to name where each
    # checkout ended up rather than the branch that was asked for. The driver
    # built its log line from the top-level "branch", which is one string
    # shared by both checkouts, and that is how a swapped pair logged as a
    # correct launch.
    results.append(
        (
            "the receipt reports where each checkout actually is",
            public.get("after_branch") == branch
            and private.get("after_branch") == INTEGRATION_BRANCH
            and public.get("after_branch") != private.get("after_branch"),
            f"public={public.get('after_branch')} "
            f"private={private.get('after_branch')}",
        )
    )

    # A refusal must say where the checkout was too, because a refusal is the
    # case where the operator reads this receipt to find out what to undo.
    git(nested, "checkout", "-q", "-b", "someones-unfinished-work")
    stuck = branch_phase_checkouts(pub, branch)
    stuck_private = {Path(r["repo"]).resolve(): r for r in stuck}[nested.resolve()]
    git(nested, "checkout", "-q", INTEGRATION_BRANCH)
    results.append(
        (
            "a refused checkout still reports the branch it is stuck on",
            (not stuck_private["ok"])
            and stuck_private.get("after_branch") == "someones-unfinished-work",
            f"ok={stuck_private.get('ok')} "
            f"after_branch={stuck_private.get('after_branch')}",
        )
    )
    return results


def _merge_gate_checks(base):
    """A receipt may only say published once a real merge put it there."""
    results = []
    pub, nested, run_dir = _publish_fixture(base, "merge-gate", 100446)
    branch = phase_branch_for(100446, "phase-100446-merge-gate.md")
    old = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        for repo in (pub, nested):
            ensure_phase_branch(repo, branch, base="main")
            # A real commit on the phase branch, in each checkout. Without one
            # the branch head equals the integration branch head, so the gate
            # would pass trivially and prove nothing.
            marker_file = "phase-work.txt"
            (repo / marker_file).write_text("the phase's product change\n")
            git(repo, "add", marker_file)
            git(repo, "commit", "-q", "-m", "phase 100446: the change")
        res = publish_phase(pub, 100446, pull_request="https://example/pr/1")
        receipt = res.get("receipt", {})
        # 1. finalize's receipt waits for a human and names the pull request.
        results.append(
            (
                "finalize records the phase as waiting for review",
                res["ok"]
                and res.get("state") == "AWAITING_REVIEW"
                and receipt.get("state") == "awaiting_review"
                and receipt.get("branch") == branch
                and receipt.get("pull_request") == "https://example/pr/1",
                f"state={res.get('state')} receipt={receipt}",
            )
        )

        # 2. Nothing is on the integration branch yet, so completion is refused.
        res2 = mark_phase_published(pub, 100446)
        results.append(
            (
                "completion is refused before the pull request merges",
                (not res2["ok"])
                and "not on the integration branch" in res2["blockers"][-1]
                and phase_receipt(pub, 100446).get("state") == "awaiting_review",
                f"blockers={res2.get('blockers')} "
                f"heads={res2.get('public_head') or ''}",
            )
        )

        # 3. A human merges. Now the same call succeeds and the board can
        #    record the phase, which is the first moment it is finished. Merged
        #    into the integration branch the receipt recorded, which is the
        #    branch mark_phase_published will look for.
        for repo in (pub, nested):
            merge_into = integration_branch(repo)
            git(repo, "checkout", "-q", merge_into)
            git(repo, "merge", "-q", "--no-edit", branch)
            git(repo, "push", "-q", "origin", merge_into)
            git(repo, "checkout", "-q", branch)
        res3 = mark_phase_published(pub, 100446)
        published = phase_receipt(pub, 100446)
        results.append(
            (
                "completion is recorded once the merge is real",
                res3["ok"]
                and res3["action"] == "marked-published"
                and published.get("state") == "published"
                and bool(published.get("public_commit"))
                and bool(published.get("private_commit"))
                and awaiting_review(pub, 100446) is None,
                f"blockers={res3.get('blockers')} "
                f"state={published.get('state')} "
                f"public={published.get('public_commit')} "
                f"private={published.get('private_commit')}",
            )
        )

        # 3b. The rewrite must leave the private tree clean. Before the fix the
        #     receipt was written and never committed, so it stayed dirt, and
        #     the next phase's publish refused over it and the driver halted.
        #     This is the smallest reproduction of the phase-100876 incident.
        nested_dirty = dirty_paths(nested)
        receipt_tracked = (
            "runs/phase-100446/publication.json"
            in git(nested, "ls-files", "runs")[1].splitlines()
        )
        results.append(
            (
                "marking a merged phase published leaves the private tree clean "
                "and the receipt committed",
                nested_dirty == [] and receipt_tracked,
                f"dirty={nested_dirty} receipt_tracked={receipt_tracked}",
            )
        )

        # 4. It is idempotent, and it never merges anything itself.
        head_before = git(pub, "rev-parse", "HEAD")[1]
        res4 = mark_phase_published(pub, 100446)
        results.append(
            (
                "recording a merge twice is a no-op and moves nothing",
                res4["ok"]
                and res4["action"] == "already-published"
                and git(pub, "rev-parse", "HEAD")[1] == head_before
                and current_branch(pub) == branch,
                f"action={res4.get('action')}",
            )
        )

        # 5. The public checkout is still on the phase branch. Nothing in the
        #    pipeline leaves it, so the branch after branch is where the
        #    published mode is supposed to clean up.
        results.append(
            (
                "the merged phase leaves the public checkout on its branch",
                current_branch(pub) == branch,
                f"branch={current_branch(pub)}",
            )
        )

        # 6. Returning to the integration branch moves it and is a no-op twice.
        res6 = return_to_integration(pub, base="main")
        results.append(
            (
                "the public checkout returns to the integration branch",
                res6["ok"]
                and res6["action"] == "returned-to-integration-branch"
                and current_branch(pub) == "main"
                and res6.get("before") == branch,
                f"branch={current_branch(pub)} {res6.get('action')}",
            )
        )
        res6b = return_to_integration(pub, base="main")
        results.append(
            (
                "returning again is a no-op",
                res6b["ok"]
                and res6b["action"] == "already-on-integration-branch"
                and current_branch(pub) == "main",
                f"branch={current_branch(pub)} {res6b.get('action')}",
            )
        )

        # 7. A dirty tree is the one case where switching branches could lose
        #    work, so it must stop rather than carry the change across.
        (pub / "uncommitted.txt").write_text("work in progress\n")
        res7 = return_to_integration(pub, base="main")
        results.append(
            (
                "a dirty working tree refuses to switch branch",
                (not res7["ok"])
                and "uncommitted changes" in res7["blockers"][-1]
                and current_branch(pub) == "main"
                and (pub / "uncommitted.txt").exists(),
                f"branch={current_branch(pub)} blockers={res7.get('blockers')}",
            )
        )
        # Untracked, so removing the file is the whole cleanup. `git rm` would
        # stage the removal and leave the tree just as dirty for the next check.
        (pub / "uncommitted.txt").unlink()

        # 8. A missing integration branch stops rather than moving nowhere.
        res8 = return_to_integration(pub, base="no-such-base")
        results.append(
            (
                "a missing integration branch refuses to switch to",
                (not res8["ok"])
                and "does not exist" in res8["blockers"][-1]
                and current_branch(pub) == "main",
                f"branch={current_branch(pub)} blockers={res8.get('blockers')}",
            )
        )
    finally:
        conventions.override({"board.branch": old})
    return results
