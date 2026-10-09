# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The publication-receipt, group-publication and custom-convention checks."""

import subprocess
from pathlib import Path

from argus.gitsync_branch import ensure_phase_branch
from argus.gitsync_git import current_branch, git, git_binary
from argus.gitsync_mode_branch import mode_branch
from argus.gitsync_modes import mode_published
from argus.gitsync_paths import group_branch, phase_branch
from argus.gitsync_publish_phase import publish_phase
from argus.gitsync_receipts import phase_receipt, receipt_for_branch
from argus.gitsync_review import open_predecessor
from argus.gitsync_selftest_fixture import BOARD_REL, _configure_self_test, _init_remote
from argus.gitsync_selftest_publish import _publish_fixture


def _publication_receipt_check(base):
    d = base / "publication-receipt"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    _configure_self_test(pub, nested)
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        store = ReservationStore(pub, branch="main")
        reservation = store.reserve(100440, "server-01")
        run_dir = nested / "runs" / "phase-100440"
        run_dir.mkdir(parents=True, exist_ok=True)
        save_reservation_file(run_dir / "reservation.json", reservation)
        (run_dir / "run.json").write_text('{"state":"completed"}\\n')
        result = publish_phase(pub, 100440)
        if not result.get("ok") or result.get("state") != "AWAITING_REVIEW":
            return False, str(result)
        receipt_path = result.get("receipt", {}).get("path")
        if not receipt_path or not Path(receipt_path).is_file():
            return False, "publication receipt was not written"
        # A receipt for an unmerged phase must not read as completion. That is
        # the whole point of a phase branch: nothing reaches the integration
        # branch until a person merges, so the phase is not finished.
        from argus.next_phase import publication_receipt_done

        if publication_receipt_done(pub, 100440):
            return False, "an unmerged phase's receipt reads as done"
        from argus.gitsync import awaiting_review

        review = awaiting_review(pub, 100440)
        if not review or review.get("branch") is None:
            return False, f"receipt does not name a branch to review: {review}"
        # The coordination board must not be able to record this phase as
        # complete, because its evidence is a receipt for an unmerged branch.
        try:
            store.set_status(reservation, "completed", evidence_path=receipt_path)
            return False, "the board accepted an unmerged phase as complete"
        except Exception:  # noqa: BLE001 - any refusal is the point
            pass
        return store.read()[0]["phases"]["100440"][
            "status"
        ] != "completed", "an unmerged phase cannot be recorded as complete"
    except Exception as exc:  # noqa: BLE001 - self-test reports the failure
        return False, str(exc)


def _group_publication_check(base):
    """One branch, one pull request, and one receipt per member of a group.

    A group publishes every member from one branch and one pull request, and each
    member keeps its own receipt under its own run directory. Both members are
    published for real here, because the property is about what the helper
    writes, not about what a fixture says it would write.

    Returns ``(ok, detail)`` like the other fixture checks.
    """
    d = base / "group-publication"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    # The declaration lives in the phase documents, so where a group is read
    # from is where it has to be written.
    roadmap = nested / "roadmap"
    roadmap.mkdir(parents=True, exist_ok=True)
    for number, behaviour in ((100020, "cache-warm"), (100030, "cache-read")):
        (roadmap / f"phase-{number:06d}-{behaviour}.md").write_text(
            f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
            "### Reservation Group\n\n`together: 100020, 100030`\n",
            encoding="utf-8",
        )
    _configure_self_test(pub, nested, roadmap=roadmap)
    members = ["100020", "100030"]
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        store = ReservationStore(pub, branch="main")
        records = store.reserve_group(members, "server-01")
        git(nested, "fetch", "-q", "origin")
        git(nested, "merge", "-q", "--ff-only", "origin/main")
        branch = group_branch(pub, members)
        made = ensure_phase_branch(pub, branch)
        if not made.get("ok"):
            return False, f"the group branch was not created: {made}"
        for record in records:
            run_dir = nested / "runs" / f"phase-{record['phase']}"
            run_dir.mkdir(parents=True, exist_ok=True)
            save_reservation_file(run_dir / "reservation.json", record)
            (run_dir / "run.json").write_text('{"state":"completed"}\n')
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "run state")
        git(nested, "push", "-q", "origin", "main")
        url = "https://github.com/example/argus/pull/7"
        first = publish_phase(pub, 100020, pull_request=url)
        if not first.get("ok"):
            return False, f"the group's first member did not publish: {first}"
        # No pull request is passed for the second member. One branch can carry
        # one pull request, so the group's own record is the only answer.
        second = publish_phase(pub, 100030)
        if not second.get("ok"):
            return False, f"the group's second member did not publish: {second}"
        receipts = {number: phase_receipt(pub, number) for number in (100020, 100030)}
        if not all(receipts.values()):
            return False, "a member published without a receipt of its own"
        if {r.get("branch") for r in receipts.values()} != {branch}:
            return False, (
                "the members did not publish on one branch: "
                f"{[r.get('branch') for r in receipts.values()]}"
            )
        if {r.get("pull_request") for r in receipts.values()} != {url}:
            return False, (
                "the members did not record the same pull request: "
                f"{[r.get('pull_request') for r in receipts.values()]}"
            )
        for number, receipt in receipts.items():
            if list(receipt.get("group") or []) != members:
                return False, (
                    f"phase {number}'s receipt does not record the "
                    f"group it belongs to: {receipt.get('group')}"
                )
        # A merged member must not take the checkout off the group branch: the
        # members that have not run yet still need it.
        half = mode_published(pub, 100020)
        if half.get("state") != "PUBLISHED-GROUP-PENDING":
            return False, (
                "a half-published group did not leave the checkout "
                f"where the rest of the group needs it: {half.get('state')}"
            )
        if current_branch(pub) != branch:
            return False, (
                f"the checkout left the group branch for {current_branch(pub)}"
            )
        done = mode_published(pub, 100030)
        if done.get("state") != "PUBLISHED":
            return (
                False,
                f"the finished group did not tidy the checkout: {done.get('state')}",
            )
        if current_branch(pub) == branch:
            return (
                False,
                "the checkout stayed on the group branch once every member was published",
            )
        return True, "one branch, one pull request, one receipt per member"
    except Exception as exc:  # noqa: BLE001 - self-test reports the failure
        return False, str(exc)


def _portable_publication_check(base):
    """Publish a custom phase through an independently named private checkout."""
    phase = 100615
    try:
        pub, nested, _run = _publish_fixture(base, "portable-publication", phase)
        private = pub.parent / "renamed-private-board"
        nested.rename(private)
        run = private / "artifacts" / f"item-{phase:06d}"
        run.parent.mkdir()
        (private / "runs" / f"phase-{phase:06d}").rename(run)
        roadmap = private / "plans"
        roadmap.mkdir()
        document = roadmap / f"release_{phase}_generic-release.md"
        document.write_text("generic release\n")
        pipeline = pub.parent / "custom-pipeline.yaml"
        pipeline.write_text(
            "inputs:\n  ticket: {type: int, required: true}\n"
            "  document: {type: str, required: true}\n"
            "run_dir: " + str(private / "artifacts" / "item-{ticket:06d}") + "\n"
        )
        _configure_self_test(
            pub,
            private,
            pipeline=pipeline,
            roadmap=roadmap,
            runs=run.parent,
            include=("release_*.md",),
            number_input="ticket",
            file_input="document",
        )
        git(private, "add", "-A")
        git(private, "commit", "-q", "-m", "custom private layout")
        git(private, "push", "-q", "origin", "main")
        branched = mode_branch(pub, phase, document.name)
        if not branched.get("ok"):
            return False, f"custom branch failed: {branched}"
        (pub / "product.txt").write_text("release\n")
        git(pub, "add", "product.txt")
        git(pub, "commit", "-q", "-m", "generic release")
        result = publish_phase(pub, phase)
        receipt = result.get("receipt", {})
        if not result.get("ok"):
            return False, f"custom publication failed: {result}"
        branch = phase_branch(document.name)
        predecessor = open_predecessor(pub, phase + 1)
        recorded = git(
            private, "show", f"HEAD:artifacts/item-{phase:06d}/publication.json"
        )
        return (
            receipt.get("path") == str(run / "publication.json")
            and phase_receipt(pub, phase).get("branch") == branch
            and receipt_for_branch(pub, branch).get("phase") == str(phase)
            and predecessor is not None
            and predecessor["phase"] == phase
            and recorded[0] == 0,
            "custom phase, typed inputs, run directory, private checkout, "
            "reservation, receipt, and predecessor use the configured layout",
        )
    except Exception as exc:  # noqa: BLE001 - the self-test reports the failure
        return False, str(exc)
