# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The receipt-carry, handoff and group-branch-collision checks."""

import json

from argus import conventions
from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import git, integration_branch
from argus.gitsync.mode_clean import mode_checkpoint
from argus.gitsync.modes import mode_published
from argus.gitsync.paths import (
    assert_group_branch_unique,
    group_branch,
    phase_branch_for,
)
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _configure_self_test,
    _gh_says,
    _init_remote,
    _StubGH,
    _write_group_phases,
)
from argus.gitsync.selftest_publish import _publish_fixture


def _receipt_carry_checks(base):
    """A merged phase's receipt must not block the next phase's publication.

    The incident this reproduces: phases 100874 and 100875 were merged while
    phase 100876 was running. The merge check marked them published, which
    rewrote their receipts but never committed them, and phase 100876's finalize
    then refused with ``working tree is dirty after phase cleanup`` and halted
    the line.

    This is the end-to-end shape: publish and merge phase one, mark it
    published, then publish a *different* phase and require it to succeed. The
    single-assertion version lives in ``_merge_gate_checks``; this one proves
    the cross-phase consequence, which no other check constructs.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        from argus.board.reservations import ReservationStore, save_reservation_file

        # 100490 is taken by the fixture itself, so the second phase under test
        # is 100491 and needs its own reservation below.
        pub, nested, _run = _publish_fixture(base, "receipt-carry", 100490)
        store = ReservationStore(pub, branch="main")
        base_branch = integration_branch(pub)
        first_branch = phase_branch_for(100490, "phase-100490-carry-first.md")
        second_branch = phase_branch_for(100491, "phase-100491-carry-second.md")

        def reserve(phase):
            git(nested, "fetch", "-q", "origin")
            git(nested, "merge", "-q", "--ff-only", "origin/main")
            rd = nested / "runs" / f"phase-{phase:06d}"
            rd.mkdir(parents=True, exist_ok=True)
            save_reservation_file(
                rd / "reservation.json", store.reserve(phase, "server-01")
            )
            (rd / "run.json").write_text('{"state":"completed"}\n')
            (rd / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
            # Phase-scoped, never `add -A`: a blanket add would sweep up the
            # merged phase's receipt and hide the very dirt this check is for.
            git(nested, "add", f"runs/phase-{phase:06d}")
            git(nested, "commit", "-q", "-m", f"run state {phase}")
            git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one: on its own branch, published, waiting for a human. The
        # fixture already reserved 100490 and wrote its run state, so it needs
        # no extra reservation here.
        ensure_phase_branch(pub, first_branch, base=base_branch)
        (pub / "first.txt").write_text("phase one\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100490: the first change")
        one = publish_phase(
            pub,
            100490,
            pull_request="https://github.com/example-org/example-repo/pull/990",
        )
        results.append(
            (
                "the first phase publishes and waits for review",
                one["ok"] and one.get("state") == "AWAITING_REVIEW",
                f"ok={one.get('ok')} state={one.get('state')} "
                f"summary={one.get('summary')}",
            )
        )

        # The human squash-merges phase one, and the merge check records it.
        integration = integration_branch(pub)
        git(pub, "checkout", "-q", integration)
        git(pub, "merge", "-q", "--squash", first_branch)
        git(pub, "commit", "-q", "-m", "phase 100490 (squashed by a human)")
        git(pub, "push", "-q", "origin", integration)
        git(nested, "checkout", "-q", integration_branch(nested))
        git(nested, "merge", "-q", "--no-edit", first_branch)
        git(nested, "push", "-q", "origin", integration_branch(nested))
        with _StubGH(_gh_says("MERGED")):
            done = mode_published(pub, 100490)
        results.append(
            (
                "the merge check marks the merged phase published",
                done["ok"]
                and done.get("state") == "PUBLISHED"
                and phase_receipt(pub, 100490).get("state") == "published",
                f"ok={done.get('ok')} state={done.get('state')} "
                f"summary={done.get('summary')}",
            )
        )

        # The next phase publishes. Before the fix the merged phase's
        # uncommitted receipt was still dirt here, so this refused with
        # "working tree is dirty after phase cleanup" and the line halted.
        ensure_phase_branch(pub, second_branch, base=base_branch)
        (pub / "second.txt").write_text("phase two\n")
        git(pub, "add", "second.txt")
        git(pub, "commit", "-q", "-m", "phase 100491: the second change")
        reserve(100491)
        two = publish_phase(
            pub,
            100491,
            pull_request="https://github.com/example-org/example-repo/pull/991",
        )
        blocked_dirty = "working tree is dirty after phase cleanup" in str(two)
        results.append(
            (
                "a different phase publishes after a merged phase is marked published",
                two["ok"]
                and two.get("state") == "AWAITING_REVIEW"
                and not blocked_dirty,
                f"ok={two.get('ok')} state={two.get('state')} "
                f"blocked_dirty={blocked_dirty} error={two.get('error')} "
                f"dirty={two.get('dirty')}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("receipt carry checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _handoff_checks(base):
    """A release handoff pushes the work in progress, and not onto the integration branch.

    Both halves matter. An owner who releases with nothing pushed hands over an
    empty branch, and one who pushes unfinished work onto the integration branch
    puts unreviewed code where everyone pulls.
    """
    d = base / "handoff"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "handoff")
    _configure_self_test(pub, pub / BOARD_REL)
    (pub / "unfinished.txt").write_text("half a phase\n", encoding="utf-8")
    on_integration = mode_checkpoint(pub)
    branch = "feature/group-cache-warm"
    made = ensure_phase_branch(pub, branch)
    if not made.get("ok"):
        return [("the handoff fixture could not cut a phase branch", False, str(made))]
    (pub / "unfinished.txt").write_text("half a phase\n", encoding="utf-8")
    pushed = mode_checkpoint(pub)
    head = git(pub, "rev-parse", "HEAD")[1]
    remote_head = git(pub, "rev-parse", f"origin/{branch}")[1]
    return [
        (
            "the integration branch is refused",
            not on_integration.get("ok")
            and "work in progress" in json.dumps(on_integration),
            f"state={on_integration.get('state')} {on_integration.get('summary')}",
        ),
        (
            "work in progress is committed and pushed on the phase branch",
            pushed.get("ok")
            and pushed.get("action") == "committed-and-pushed"
            and remote_head == head,
            f"ok={pushed.get('ok')} action={pushed.get('action')} "
            f"remote={remote_head[:8]} head={head[:8]}",
        ),
    ]


def _group_branch_collision_checks(base):
    """Two groups that derive one branch name are refused, and one group is not.

    A group branch carries no phase number, so the lowest member's behaviour word
    is all that separates one group's branch from another's.
    """
    results = []
    mine = ["100020", "100021"]
    d = base / "group-name"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "group-name")
    roadmap = pub / "roadmap"
    roadmap.mkdir()
    _write_group_phases(roadmap, (100020, 100021), "cache-warm")
    _write_group_phases(roadmap, (100040, 100041), "warm-cache")
    _configure_self_test(pub, pub / BOARD_REL, roadmap=roadmap)
    branch = group_branch(pub, mine)
    same = assert_group_branch_unique(pub, branch, mine)
    results.append(
        (
            "a group is not its own collision, and its branch comes from its lowest member",
            same.get("ok") and branch == "feature/group-cache-warm",
            f"ok={same.get('ok')} branch={branch} {same.get('summary')}",
        )
    )

    d = base / "group-name-clash"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "group-name-clash")
    roadmap = pub / "roadmap"
    roadmap.mkdir()
    _write_group_phases(roadmap, (100020, 100021), "cache-warm")
    _write_group_phases(roadmap, (100040, 100041), "cache-warm")
    _configure_self_test(pub, pub / BOARD_REL, roadmap=roadmap)
    clash = assert_group_branch_unique(pub, branch, ["100040", "100041"])
    results.append(
        (
            "two groups deriving one branch are refused",
            not clash.get("ok") and "also the branch for group" in json.dumps(clash),
            f"{clash.get('summary')} {json.dumps(clash)}",
        )
    )
    return results
