# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The stale-receipt and tree-proof checks."""

import json

from argus import conventions
from argus.gitsync_branch import ensure_phase_branch
from argus.gitsync_git import git, integration_branch
from argus.gitsync_mode_branch import mode_branch
from argus.gitsync_modes import mode_published
from argus.gitsync_paths import phase_branch_for
from argus.gitsync_publish_phase import publish_phase
from argus.gitsync_receipts import (
    _head_carried_locally,
    head_tree_on_integration,
    phase_receipt,
)
from argus.gitsync_review import open_predecessor
from argus.gitsync_selftest_fixture import _gh_says, _StubGH
from argus.gitsync_selftest_publish import _publish_fixture


def _stale_receipt_checks(base):
    """A merged phase whose receipt still says awaiting_review must not block.

    This is the state the line actually stalls in, and no other check here
    builds it. Every other fixture for a phase that published also leaves the
    merge check to resolve it, so the one state that deadlocks the line — a
    phase whose run finished, whose pull request a human squash-merged, and
    whose receipt nobody has updated — is a state the suite never constructed.

    Two things made it unconstructible, and both are the bug rather than the
    fixture's fault:

    - The merge check was only ever told about phases the selector had *not*
      finished, so a phase that ran to completion was never checked, and its
      receipt stayed ``awaiting_review`` for ever.
    - The receipt's parsed pull request reference was written only at publish
      time, so a receipt published before that field existed could never be
      confirmed by GitHub at all. Its only remaining proof was the commit-id
      test, which a squash merge has already deleted.

    So the fixture here is a finished run plus a squash-merged pull request
    plus a receipt with no parsed reference: the real shape, built from the
    real entry points. The phase after it must branch from the integration
    branch, not from a branch whose content is already there.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        # The fixture takes 100484 for its own coordination board, so the two
        # phases under test are 100485 and 100486.
        pub, nested, _run = _publish_fixture(base, "stale-receipt", 100484)
        store = ReservationStore(pub, branch="main")
        base_branch = integration_branch(pub)
        first = phase_branch_for(100485, "phase-100485-stale-receipt.md")
        second = phase_branch_for(100486, "phase-100486-after-stale.md")

        (nested / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested / "roadmap" / "phase-100485-stale-receipt.md").write_text("x\n")
        (nested / "roadmap" / "phase-100486-after-stale.md").write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "roadmap")
        git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one runs to completion and publishes. The run finishing is what
        # makes the selector treat it as done, so this is the state that has to
        # be built before the merge, not after.
        mode_branch(pub, 100485, "phase-100485-stale-receipt.md")
        (pub / "first.txt").write_text("phase one\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100485: the first change")
        rd = nested / "runs" / "phase-100485"
        rd.mkdir(parents=True, exist_ok=True)
        save_reservation_file(
            rd / "reservation.json", store.reserve(100485, "server-01")
        )
        (rd / "run.json").write_text('{"state":"completed"}\n')
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "run state 100485")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        one = publish_phase(
            pub,
            100485,
            pull_request="https://github.com/example-org/example-repo/pull/980",
        )
        results.append(
            (
                "the phase under test published and is waiting for a human",
                one["ok"]
                and one.get("state") == "AWAITING_REVIEW"
                and phase_receipt(pub, 100485).get("state") == "awaiting_review",
                f"ok={one.get('ok')} state={one.get('state')}",
            )
        )

        # The human squash-merges it. The commit the phase pushed is gone from
        # history, which is precisely why the commit-id test cannot see this.
        git(pub, "checkout", "-q", base_branch)
        git(pub, "merge", "-q", "--squash", first)
        git(pub, "commit", "-q", "-m", "phase 100485 (squashed by a human)")
        git(pub, "push", "-q", "origin", base_branch)
        results.append(
            (
                "the squash really did rewrite history, so the commit-id test alone "
                "cannot see the merge",
                not _head_carried_locally(
                    pub, phase_receipt(pub, 100485)["public_head"], (base_branch,)
                )
                and (pub / "first.txt").is_file(),
                "the phase head is no longer an ancestor of the integration branch",
            )
        )

        # A receipt written before the reference was recorded. This is what the
        # pipeline's own older receipts look like, and it is the case where
        # GitHub is never asked because there is no number to ask about.
        receipt_path = rd / "publication.json"
        stale = json.loads(receipt_path.read_text())
        stale.pop("pull_request_ref", None)
        stale["pull_request"] = "https://github.com/example-org/example-repo/pull/980"
        receipt_path.write_text(json.dumps(stale, indent=2, sort_keys=True) + "\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "receipt without a parsed reference")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        results.append(
            (
                "the receipt under test carries no parsed reference, so the old "
                "code never asks GitHub about it",
                (phase_receipt(pub, 100485) or {}).get("pull_request_ref") is None
                and bool((phase_receipt(pub, 100485) or {}).get("pull_request")),
                f"ref={(phase_receipt(pub, 100485) or {}).get('pull_request_ref')}",
            )
        )

        # GitHub knows the merge. The number is in the URL and nowhere else.
        with _StubGH(_gh_says("MERGED", "2026-06-01T00:00:00Z")) as gh:
            done = mode_published(pub, 100485)
        results.append(
            (
                "a merged phase with a stale receipt is marked published even "
                "though its receipt recorded no pull request reference",
                done["ok"]
                and done.get("state") == "PUBLISHED"
                and phase_receipt(pub, 100485).get("state") == "published",
                f"ok={done.get('ok')} state={done.get('state')} "
                f"gh_calls={len(gh.calls)} "
                f"blockers={[b for r in done.get('repos', []) for b in r.get('blockers', [])]}",
            )
        )
        results.append(
            (
                "GitHub was asked about the pull request named only in the URL",
                any("980" in " ".join(str(a) for a in call) for call in gh.calls),
                f"calls={gh.calls}",
            )
        )

        # The point of the whole exercise: the next phase is no longer stacked
        # on a branch whose content is already on the integration branch.
        results.append(
            (
                "after the merge is recorded, the next phase is not stacked on the "
                "merged phase's branch",
                open_predecessor(pub, 100486) is None,
                f"predecessor={open_predecessor(pub, 100486)}",
            )
        )
        two = mode_branch(pub, 100486, "phase-100486-after-stale.md")
        results.append(
            (
                "the next phase branches from the integration branch and inherits "
                "the merged phase's code",
                two["ok"]
                and two.get("base") == base_branch
                and "first.txt"
                in git(pub, "ls-tree", "--name-only", second)[1].split(),
                f"ok={two.get('ok')} base={two.get('base')} "
                f"predecessor={two.get('predecessor')}",
            )
        )

        # The harder case, and the one that actually stalled the line: GitHub
        # cannot be asked at all. The receipt stays stale, because only GitHub or
        # the pull request's own commit may write "published". The next phase
        # must still branch from master rather than from a dead branch, because
        # the code it needs is already there. This is the content proof, and it
        # is the reason the line recovers on a host with no working gh.
        # The fixture takes 100612 for its own coordination board, so the two
        # phases under test here are 100613 and 100614.
        pub3, nested3, _run3 = _publish_fixture(base, "stale-no-gh", 100612)
        base3 = integration_branch(pub3)
        branch3 = phase_branch_for(100613, "phase-100613-stale-no-gh.md")
        next3 = phase_branch_for(100614, "phase-100614-after-no-gh.md")
        (nested3 / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested3 / "roadmap" / "phase-100613-stale-no-gh.md").write_text("x\n")
        (nested3 / "roadmap" / "phase-100614-after-no-gh.md").write_text("x\n")
        git(nested3, "add", "-A")
        git(nested3, "commit", "-q", "-m", "roadmap")
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        mode_branch(pub3, 100613, "phase-100613-stale-no-gh.md")
        (pub3 / "landed.txt").write_text("already on master\n")
        git(pub3, "add", "landed.txt")
        git(pub3, "commit", "-q", "-m", "phase 100613: the change")
        rd3 = nested3 / "runs" / "phase-100613"
        rd3.mkdir(parents=True, exist_ok=True)
        # This fixture has its own remotes, so it needs its own board. The store
        # built for the first fixture points at different checkouts, and a
        # reservation written through it lands nowhere this phase can publish
        # from — which shows up as a publish that silently writes no receipt.
        store3 = ReservationStore(pub3, branch="main")
        save_reservation_file(
            rd3 / "reservation.json", store3.reserve(100613, "server-01")
        )
        (rd3 / "run.json").write_text('{"state":"completed"}\n')
        git(nested3, "add", "-A")
        git(nested3, "commit", "-q", "-m", "run state 100613")
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        publish_phase(
            pub3,
            100613,
            pull_request="https://github.com/example-org/example-repo/pull/981",
        )
        git(pub3, "checkout", "-q", base3)
        git(pub3, "merge", "-q", "--squash", branch3)
        git(pub3, "commit", "-q", "-m", "phase 100613 (squashed)")
        git(pub3, "push", "-q", "origin", base3)
        # The private side is merged with a real merge commit, the way the
        # pipeline pushes it. Only the public side is squashed, because that is
        # the side whose proof is in question here.
        git(nested3, "checkout", "-q", integration_branch(nested3))
        git(nested3, "merge", "-q", "--no-edit", branch3)
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        with _StubGH({"returncode": 4}):
            nogh = mode_published(pub3, 100613)
        results.append(
            (
                "with GitHub unreachable the receipt is not rewritten, because "
                "only GitHub or the pull request's own commit may say merged",
                (not nogh["ok"])
                and (phase_receipt(pub3, 100613) or {}).get("state")
                == "awaiting_review",
                f"ok={nogh.get('ok')} "
                f"state={(phase_receipt(pub3, 100613) or {}).get('state')}",
            )
        )
        results.append(
            (
                "with GitHub unreachable the next phase still branches from the "
                "integration branch, because the code is already on it",
                open_predecessor(pub3, 100614) is None,
                f"predecessor={open_predecessor(pub3, 100614)}",
            )
        )
        three = mode_branch(pub3, 100614, "phase-100614-after-no-gh.md")
        results.append(
            (
                "a phase launched on a host that cannot ask GitHub inherits the "
                "merged phase's code instead of refusing to launch",
                three["ok"]
                and three.get("base") == base3
                and "landed.txt"
                in git(pub3, "ls-tree", "--name-only", next3)[1].split(),
                f"ok={three.get('ok')} base={three.get('base')} "
                f"blockers={[b for r in three.get('repos', []) for b in r.get('blockers', [])]}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("stale-receipt checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _tree_proof_checks(base):
    """The tree a phase published must be findable on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see the merge. What survives is the content: the squash commit's
    tree is the phase branch's tree. That is the proof this checks, and it is
    the one that still works when GitHub cannot be asked at all.
    """
    results = []
    pub, _nested, _run = _publish_fixture(base, "tree-proof", 100482)
    integration = integration_branch(pub)
    branch = phase_branch_for(100482, "phase-100482-tree-proof.md")
    ensure_phase_branch(pub, branch, base=integration)
    for step in range(3):
        (pub / f"part-{step}.txt").write_text(f"work {step}\n")
        git(pub, "add", f"part-{step}.txt")
        git(pub, "commit", "-q", "-m", f"phase 100482 step {step}")
    head = git(pub, "rev-parse", "HEAD")[1]
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
    unmerged_head = git(pub, "rev-parse", "HEAD")[1]
    git(pub, "checkout", "-q", integration)
    results.append(
        (
            "content that was never merged is not found on the integration branch",
            not head_tree_on_integration(pub, unmerged_head, integration),
            f"head={unmerged_head[:12]}",
        )
    )
    return results
