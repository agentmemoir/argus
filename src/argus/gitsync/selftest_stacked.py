# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The integration-branch merge and stacked-predecessor checks."""

import subprocess

from argus.gitsync.branch import branch_phase_checkouts, ensure_phase_branch
from argus.gitsync.git import current_branch, git, git_binary
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.review import awaiting_review, open_predecessor
from argus.gitsync.selftest_fixture import _init_remote, _remote_commit
from argus.gitsync.selftest_publish import _publish_fixture
from argus.gitsync.sync import resync_integration


def _sequencing_integration_checks(base, results):
    # --- Item 5: the integration branch merged into the phase branch. ---
    d = base / "resync"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    ensure_phase_branch(work, "feature/resync", base="main")
    (work / "phase.txt").write_text("the phase's work\n")
    git(work, "add", "phase.txt")
    git(work, "commit", "-q", "-m", "phase commit")
    before = git(work, "rev-parse", "HEAD")[1]
    noop = resync_integration(work, base="main")
    results.append(
        (
            "a resync with an unmoved integration branch does not move the phase "
            "branch",
            noop["ok"]
            and noop["action"] == "phase-ahead"
            and git(work, "rev-parse", "HEAD")[1] == before,
            f"action={noop.get('action')} relation={noop.get('relation')}",
        )
    )
    # Integration branch moves, disjoint paths: a plain merge, no rebase.
    _remote_commit(remote, d, "w")
    clean = resync_integration(work, base="main")
    parents = git(work, "rev-list", "--parents", "-n1", "HEAD")[1].split()
    results.append(
        (
            "a resync merges an advanced integration branch without rebasing",
            clean["ok"]
            and clean["action"] == "merged"
            and len(parents) == 3
            and (work / "phase.txt").is_file()
            and (work / "a.txt").is_file(),
            f"action={clean.get('action')} parents={parents}",
        )
    )
    # Now both sides own the same path with different content: a real
    # conflict, which must be left in place and named so an agent can
    # finish it rather than the pipeline cleaning it up.
    (work / "clash.txt").write_text("the phase's side\n")
    git(work, "add", "clash.txt")
    git(work, "commit", "-q", "-m", "phase takes a side on clash.txt")
    # A second throwaway clone, because `_remote_commit` only ever edits
    # a.txt and this case needs the integration branch to take the other
    # side on a path the phase already owns.
    other = d / "other-w2"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(other)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(other, "config", "user.email", "sync-test@example.com")
    git(other, "config", "user.name", "sync test")
    (other / "clash.txt").write_text("the integration branch's side\n")
    git(other, "add", "clash.txt")
    git(other, "commit", "-q", "-m", "integration takes a side")
    git(other, "push", "-q", "origin", "main")
    clash = resync_integration(work, base="main")
    results.append(
        (
            "a resync conflict is left in place with the paths named",
            (not clash["ok"])
            and clash.get("conflicted") == ["clash.txt"]
            and (work / ".git" / "MERGE_HEAD").exists()
            and "<<<<<<<" in (work / "clash.txt").read_text()
            and "git commit" in (clash.get("hint") or ""),
            f"action={clash.get('action')} "
            f"conflicted={clash.get('conflicted')} "
            f"blockers={clash.get('blockers')}",
        )
    )
    # The agent is expected to finish it; assert the recipe the finalize
    # prompt gives actually works, so the instruction is not folklore.
    (work / "clash.txt").write_text("resolved by the phase agent\n")
    git(work, "add", "clash.txt")
    rc_finish, _, err_finish = git(work, "commit", "--no-edit")
    results.append(
        (
            "the conflict the resync leaves can be finished the way the prompt "
            "instructs",
            rc_finish == 0 and not (work / ".git" / "MERGE_HEAD").exists(),
            f"rc={rc_finish} err={err_finish}",
        )
    )
    # The private checkout is always on the integration branch, so it is a
    # named no-op rather than a second integration.
    results.append(
        (
            "a checkout already on the integration branch is a named no-op",
            resync_integration(work, base="main")["action"]
            in {"already-on-integration-branch", "phase-ahead"},
            "reported, not dropped",
        )
    )


def _sequencing_stack_checks(base, results):
    # --- Item 3 and 7: a phase built on an open predecessor. ---
    pub3, nested3, _run3 = _publish_fixture(base, "stack-base", 100453)
    branch_first = "feature/stack-base-first"
    branch_second = "feature/stack-base-second"
    ensure_phase_branch(pub3, branch_first, base="main")
    (pub3 / "first.txt").write_text("the first phase's code\n")
    git(pub3, "add", "first.txt")
    git(pub3, "commit", "-q", "-m", "phase 100453: the first change")
    first = publish_phase(
        pub3,
        100453,
        pull_request="https://github.com/example-org/example-repo/pull/901",
    )
    results.append(
        (
            "a phase with no open predecessor publishes with no warning",
            first.get("ok") and "warning" not in first,
            f"warning={first.get('warning')}",
        )
    )
    predecessor = open_predecessor(pub3, 100454)
    results.append(
        (
            "an open predecessor is found for the next phase",
            (predecessor or {}).get("phase") == 100453
            and predecessor["branch"] == branch_first,
            f"predecessor={predecessor}",
        )
    )
    # The checkout is left on the predecessor's branch by publish_phase,
    # which is the state the driver is in when it branches the next phase,
    # so the fixture must not move it. ensure_phase_branch refuses to switch
    # a checkout that is on neither the phase branch nor its base, and that
    # refusal is correct here.
    made = branch_phase_checkouts(
        pub3, branch_second, base=(predecessor or {}).get("branch")
    )
    first_on_second = git(
        pub3, "rev-list", "--count", f"{branch_first}..{branch_second}"
    )[1].strip()
    results.append(
        (
            "the next phase branch is cut from the open predecessor, so it "
            "contains the predecessor's code",
            all(m["ok"] for m in made)
            and first_on_second == "0"
            and (pub3 / "first.txt").is_file(),
            f"commits ahead of predecessor={first_on_second} "
            f"results={[m.get('action') for m in made]} "
            f"blockers={[m.get('blockers') for m in made]}",
        )
    )
    # The private checkout stays on the integration branch even while the
    # public one is built on a predecessor. Asserted, because passing the
    # predecessor's base to it stops every launch and reads as a broken
    # pipeline rather than as a wiring mistake.
    private_actions = [m.get("action") for m in made[1:]]
    results.append(
        (
            "the private checkout is not moved onto the predecessor's branch",
            private_actions in ([], ["stays-on-integration-branch"])
            and current_branch(nested3) == "main",
            f"actions={private_actions} branch={current_branch(nested3)}",
        )
    )
    # Without the predecessor as base the phase would be built from the
    # integration branch, which does not have the code. Compared directly
    # rather than by creating a second branch, because the checkout is
    # deliberately still on the predecessor's branch here and moving it
    # would test the wrong thing.
    on_integration = git(pub3, "ls-tree", "--name-only", "main")[1].split()
    on_phase = git(pub3, "ls-tree", "--name-only", branch_second)[1].split()
    results.append(
        (
            "cutting from the integration branch instead would leave the "
            "predecessor's code behind",
            "first.txt" not in on_integration and "first.txt" in on_phase,
            f"integration={sorted(on_integration)} phase={sorted(on_phase)}",
        )
    )
    # Item 7: publishing on top of an unmerged predecessor warns. The second
    # phase needs its own reservation, because publication is fenced per
    # phase and that fence is the point of the helper. This runs while the
    # predecessor's branch still exists, because the receipt's record of
    # which commit it was stacked on is only obtainable then.
    from argus.board.reservations import ReservationStore, save_reservation_file

    store3 = ReservationStore(pub3, branch="main")
    git(nested3, "fetch", "-q", "origin")
    git(nested3, "merge", "-q", "--ff-only", "origin/main")
    run_dir3 = nested3 / "runs" / "phase-100454"
    run_dir3.mkdir(parents=True, exist_ok=True)
    save_reservation_file(
        run_dir3 / "reservation.json", store3.reserve(100454, "server-01")
    )
    (run_dir3 / "run.json").write_text('{"state":"completed"}\n')
    (run_dir3 / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
    git(nested3, "add", "-A")
    git(nested3, "commit", "-q", "-m", "run state for phase 100454")
    git(nested3, "push", "-q", "origin", "main")
    git(pub3, "checkout", "-q", branch_second)
    second_res = publish_phase(
        pub3,
        100454,
        pull_request="https://github.com/example-org/example-repo/pull/902",
    )
    carries = second_res.get("carries_open_phase") or {}
    second_receipt = phase_receipt(pub3, 100454)
    stacked = second_receipt.get("stacked_on") or {}
    results.append(
        (
            "publishing on top of an unmerged predecessor warns that the pull "
            "request carries that work",
            second_res.get("ok")
            and carries.get("branch") == branch_first
            and "still waiting for review" in (second_res.get("warning") or ""),
            f"carries={carries} warning={second_res.get('warning')}",
        )
    )
    results.append(
        (
            "the receipt records the predecessor it was stacked on, with the "
            "commit, so a rewritten predecessor is detectable later",
            stacked.get("branch") == branch_first
            and stacked.get("phase") == "100453"
            and stacked.get("head") == git(pub3, "rev-parse", branch_first)[1],
            f"stacked_on={stacked}",
        )
    )

    # A predecessor naming a branch that is gone stops the launch rather
    # than quietly falling back to the integration branch. Checked through
    # the lookup the refusal uses, not through open_predecessor: that one
    # returns the *highest* open predecessor, which is now phase 100454, so
    # asking it about 100453 would prove nothing.
    git(pub3, "checkout", "-q", "main")
    git(pub3, "branch", "-D", branch_first)
    git(pub3, "push", "-q", "origin", "--delete", branch_first)
    still_named = awaiting_review(pub3, 100453)
    highest = open_predecessor(pub3, 100455)
    results.append(
        (
            "a predecessor whose branch is gone is still reported, so the "
            "launch step can refuse instead of falling back to the "
            "integration branch",
            (still_named or {}).get("branch") == branch_first
            and git(pub3, "rev-parse", "--verify", f"refs/heads/{branch_first}")[0] != 0
            and (highest or {}).get("branch") == branch_second,
            f"awaiting_review(100453)={still_named} open_predecessor(100455)={highest}",
        )
    )
