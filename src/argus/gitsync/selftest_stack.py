# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The two-phase stack checks."""

import subprocess
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.gitsync.git import current_branch, git, git_binary, integration_branch
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.modes import mode_published
from argus.gitsync.paths import phase_branch_for
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.review import awaiting_review, open_predecessor
from argus.gitsync.selftest_fixture import _gh_says, _reserve_phase, _StubGH
from argus.gitsync.selftest_publish import _publish_fixture
from argus.gitsync.sync import mode_resync


def _two_phase_stack_checks(base: Path) -> list[tuple[str, object, str]]:
    """Two phases open at once, driven through every step the pipeline uses.

    Every other check here exercises one decision. This one walks the sequence
    the change actually produces — first phase published and waiting, second
    phase branched off it, the integration branch moving underneath both, second
    phase published carrying the first — and asserts the state at each step.

    It is a check rather than a driver run: the tmux window, the lock and the
    agent harness stay stubbed, but every git operation, every receipt and every
    refusal is the real code against real remotes.
    """
    results: list[tuple[str, object, str]] = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # 100469 is taken by the fixture itself; the two phases under test
        # are 100470 and 100471, so each needs its own reservation below.
        pub, nested, _run = _publish_fixture(base, "stack-e2e", 100469)
        base_branch = integration_branch(pub)
        first_branch = phase_branch_for(100470, "phase-100470-stack-first.md")
        second_branch = phase_branch_for(100471, "phase-100471-stack-second.md")
        # 100472 and 100473 are the two phases the integration-branch launch is
        # checked against, one with a clean tree and one carrying uncommitted
        # work. They are past the two phases above so neither is a predecessor of
        # the other.
        third_phases = "phase-100472-evidence-freshness-sweep.md"

        def claim(phase: int) -> None:
            """Claim the phase before a fenced mode reads the board.

            --mode branch checks the fence first, so the launch has to be a
            really claimed one. The claim is made before the phase runs, which
            is where the engine makes it, and the finished run state is landed
            separately by ``finish`` below.
            """
            _reserve_phase(pub, phase, sync=nested)

        def finish(phase: int) -> None:
            """Write the finished run state and land it, after the phase's work."""
            git(nested, "fetch", "-q", "origin")
            git(nested, "merge", "-q", "--ff-only", "origin/main")
            rd = nested / "runs" / f"phase-{phase:06d}"
            (rd / "run.json").write_text('{"state":"completed"}\n')
            (rd / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
            git(nested, "add", "-A")
            git(nested, "commit", "-q", "-m", f"run state {phase}")
            git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one: branched from the integration branch, worked on,
        # published, waiting for a human.
        (nested / "phases").mkdir(parents=True, exist_ok=True)
        (nested / "phases" / "phase-100470-stack-first.md").write_text("x\n")
        (nested / "phases" / "phase-100471-stack-second.md").write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "phases")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        claim(100470)
        one_branch = mode_branch(pub, 100470, "phase-100470-stack-first.md")
        results.append(
            (
                "with nothing open, a phase branches from the integration branch",
                one_branch["ok"]
                and one_branch.get("base") == base_branch
                and one_branch.get("predecessor") is None,
                f"ok={one_branch.get('ok')} base={one_branch.get('base')} "
                f"predecessor={one_branch.get('predecessor')}",
            )
        )
        (pub / "first.txt").write_text("phase one's code\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100470: the first change")
        finish(100470)
        one = publish_phase(
            pub,
            100470,
            pull_request="https://github.com/example-org/example-repo/pull/970",
        )
        results.append(
            (
                "phase one publishes and waits, carrying no earlier phase",
                one["ok"]
                and one.get("state") == "AWAITING_REVIEW"
                and not one.get("carries_open_phase")
                and not one.get("warning"),
                f"ok={one.get('ok')} state={one.get('state')} "
                f"warning={one.get('warning')}",
            )
        )

        # Phase two starts while phase one is still open.
        claim(100471)
        two_branch = mode_branch(pub, 100471, "phase-100471-stack-second.md")
        results.append(
            (
                "phase two branches off phase one's open branch, not off the "
                "integration branch, and inherits its code",
                two_branch["ok"]
                and two_branch.get("base") == first_branch
                and "first.txt"
                in git(pub, "ls-tree", "--name-only", second_branch)[1].split(),
                f"ok={two_branch.get('ok')} base={two_branch.get('base')} "
                f"tree={sorted(git(pub, 'ls-tree', '--name-only', second_branch)[1].split())}",
            )
        )

        # The integration branch moves underneath the open phase, from another
        # machine: exactly the situation the resync exists for.
        other = base / "stack-e2e-other"
        remote_url = git(pub, "remote", "get-url", "origin")[1]
        subprocess.run(
            [git_binary(), "clone", "-q", remote_url, str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(other, "config", "user.email", "e2e@example.com")
        git(other, "config", "user.name", "e2e")
        git(other, "checkout", "-q", base_branch)
        (other / "from-elsewhere.txt").write_text("someone else's commit\n")
        git(other, "add", "from-elsewhere.txt")
        git(other, "commit", "-q", "-m", "another machine lands a change")
        git(other, "push", "-q", "origin", base_branch)
        resynced = mode_resync(pub)
        repos: list[dict[str, Any]] = resynced.get("repos", [])
        public_resync: dict[str, Any] = next(
            (r for r in repos if Path(r.get("repo", "")).resolve() == pub.resolve()),
            dict[str, Any](),
        )
        results.append(
            (
                "a commit that landed on the integration branch while a phase was "
                "open is merged into that phase's branch",
                resynced["ok"]
                and public_resync.get("action") == "merged"
                and (pub / "from-elsewhere.txt").is_file()
                and (pub / "first.txt").is_file(),
                f"action={public_resync.get('action')} "
                f"actions={[r.get('action') for r in resynced.get('repos', [])]}",
            )
        )

        (pub / "second.txt").write_text("phase two's code\n")
        git(pub, "add", "second.txt")
        git(pub, "commit", "-q", "-m", "phase 100471: the second change")
        finish(100471)
        two = publish_phase(
            pub,
            100471,
            pull_request="https://github.com/example-org/example-repo/pull/971",
        )
        carried: dict[str, Any] = two.get("carries_open_phase") or {}
        results.append(
            (
                "phase two publishes carrying phase one's unmerged work, and says so",
                two["ok"]
                and carried.get("branch") == first_branch
                and "still waiting for review" in (two.get("warning") or ""),
                f"carries={carried} warning={two.get('warning')}",
            )
        )

        # The human squash-merges phase one, which is what GitHub's button does
        # by default and the case the commit-id check alone cannot see.
        git(pub, "checkout", "-q", base_branch)
        git(pub, "merge", "-q", "--squash", first_branch)
        git(pub, "commit", "-q", "-m", "phase 100470 (squashed by a human)")
        git(pub, "push", "-q", "origin", base_branch)
        with _StubGH(_gh_says("MERGED")):
            first_done = mode_published(pub, 100470)
        results.append(
            (
                "a squash-merged first phase resolves the check and tidies the "
                "checkout",
                first_done["ok"]
                and first_done.get("state") == "PUBLISHED"
                and cast("dict[str, Any]", phase_receipt(pub, 100470)).get("state")
                == "published"
                and current_branch(pub) == base_branch,
                f"ok={first_done.get('ok')} state={first_done.get('state')} "
                f"branch={current_branch(pub)}",
            )
        )

        # Phase two is still open, still stacked, and the next phase's base is
        # still phase two rather than phase one.
        results.append(
            (
                "phase two stays open after phase one merges, and the next phase "
                "would stack on phase two",
                awaiting_review(pub, 100471) is not None
                and (open_predecessor(pub, 100472) or {}).get("branch")
                == second_branch,
                f"awaiting_review(100471)={awaiting_review(pub, 100471)} "
                f"next base={(open_predecessor(pub, 100472) or {}).get('branch')}",
            )
        )

        # The integration branch holds the checkout again. That is what the
        # workflow asks a person to do once a pull request is open, and what the
        # tidy step does once a phase is published, so it is the ordinary state
        # between two phases and not an edge case. The next phase still stacks on
        # phase two, so the launch has to move the checkout off the integration
        # branch by itself.
        #
        # The check above this one is the one that let the bug through: it
        # branched phase two while the checkout was still sitting on phase one's
        # branch, which is a state the pipeline only passes through. It never
        # asked what happens after something returns the checkout, so every fix
        # built on it passed the self-test and stopped on the real machine.
        (nested / "phases" / third_phases).write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "phases")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        third_branch = phase_branch_for(100472, third_phases)
        started_on_integration = current_branch(pub) == base_branch
        claim(100472)
        three = mode_branch(pub, 100472, third_phases)
        results.append(
            (
                "a phase launches onto an open predecessor's branch while the "
                "checkout sits on the integration branch, with no person moving it",
                started_on_integration
                and three["ok"]
                and three.get("base") == second_branch
                and current_branch(pub) == third_branch
                and "second.txt"
                in git(pub, "ls-tree", "--name-only", third_branch)[1].split(),
                f"started_on_integration={started_on_integration} "
                f"ok={three.get('ok')} base={three.get('base')} "
                f"branch={current_branch(pub)} summary={three.get('summary')}",
            )
        )

        # The same launch with uncommitted work on the integration branch is a
        # stop, not a switch. Cutting the branch from a different commit would
        # reinterpret that work against a tree it was never written for, which is
        # the one way this move can lose someone's changes.
        git(pub, "checkout", "-q", base_branch)
        (pub / "uncommitted.txt").write_text("work in progress\n")
        fourth_phases = "phase-100473-guard-the-guard.md"
        (nested / "phases" / fourth_phases).write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "phases")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        claim(100473)
        four = mode_branch(pub, 100473, fourth_phases)
        results.append(
            (
                "uncommitted work on the integration branch still stops the launch",
                (not four["ok"])
                and current_branch(pub) == base_branch
                and (pub / "uncommitted.txt").read_text() == "work in progress\n"
                and git(
                    pub,
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{phase_branch_for(100473, fourth_phases)}",
                )[0]
                != 0,
                f"ok={four.get('ok')} branch={current_branch(pub)} "
                f"summary={four.get('summary')}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("two-phase stack checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


__all__ = ["_two_phase_stack_checks"]
