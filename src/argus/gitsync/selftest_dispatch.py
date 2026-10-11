# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The tidy and dispatch checks."""

from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import current_branch, git, integration_branch
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.modes import mode_published, mode_tidy
from argus.gitsync.paths import phase_branch_for
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.selftest_publish import _publish_fixture
from argus.gitsync.sync import mode_resync
from argus.gitsync.tidy import tidy_published_checkout


def _tidy_and_dispatch_checks(base: Path) -> list[tuple[str, object, str]]:
    """The mode dispatches, and the self-healing tidy that replaces the retry.

    These are the paths whose behaviour used to be untested, because the mode
    bodies lived inline in ``main`` where no self-test could reach them — and
    one of those paths is the difference between a recoverable hiccup and a
    pipeline that never starts another phase again.
    """
    results: list[tuple[str, object, str]] = []
    old_branch: str = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # --- mode_resync drives both checkouts through one dispatch. ---
        pub, nested, _run = _publish_fixture(base, "dispatch-resync", 100455)
        branch = "feature/dispatch-resync"
        ensure_phase_branch(pub, branch, base="main")
        (pub / "work.txt").write_text("the phase's work\n")
        git(pub, "add", "work.txt")
        git(pub, "commit", "-q", "-m", "phase 100455: the change")
        disp = mode_resync(pub)
        role_paths = {pub.resolve(): "public", nested.resolve(): "private"}
        roles = [
            role_paths.get(Path(r.get("repo", "")).resolve(), "unknown")
            for r in disp.get("repos", [])
        ]
        results.append(
            (
                "the resync dispatch reports both checkouts, and the private one "
                "as a named no-op rather than dropping it",
                disp["ok"]
                and len(roles) == 2
                and set(roles) == {"public", "private"}
                and any(
                    r.get("action")
                    in {"phase-ahead", "up-to-date", "already-on-integration-branch"}
                    for r in disp["repos"]
                    if Path(r.get("repo", "")).resolve() == nested.resolve()
                ),
                f"ok={disp.get('ok')} roles={roles} "
                f"actions={[r.get('action') for r in disp.get('repos', [])]}",
            )
        )

        # --- mode_branch refuses when the predecessor's branch is gone. ---
        # The refusal used to live inline in main() and had no check at all.
        pub2, nested2, _run2 = _publish_fixture(base, "dispatch-branch", 100456)
        first = "feature/dispatch-branch-first"
        ensure_phase_branch(pub2, first, base="main")
        (pub2 / "first.txt").write_text("the first phase's code\n")
        git(pub2, "add", "first.txt")
        git(pub2, "commit", "-q", "-m", "phase 100456: the first change")
        publish_phase(pub2, 100456)
        (nested2 / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested2 / "roadmap" / "phase-100457-dispatch-next.md").write_text("x\n")
        (nested2 / "roadmap" / "phase-100458-dispatch-after.md").write_text("x\n")
        git(nested2, "add", "-A")
        git(nested2, "commit", "-q", "-m", "roadmap for the next phases")
        git(nested2, "push", "-q", "origin", "main")
        # The branch comes from the roadmap file's behaviour, not the phase
        # number, so the test asks for the same branch the mode will create
        # rather than naming one and hoping they agree.
        second = phase_branch_for(100457, "phase-100457-dispatch-next.md")
        good = mode_branch(pub2, 100457, "phase-100457-dispatch-next")
        results.append(
            (
                "the branch dispatch cuts the next phase from the open predecessor",
                good["ok"]
                and good.get("predecessor", {}).get("branch") == first
                and "first.txt"
                in git(pub2, "ls-tree", "--name-only", second)[1].split(),
                f"ok={good.get('ok')} base={good.get('base')} branch={second} "
                f"tree={git(pub2, 'ls-tree', '--name-only', second)[1].split()}",
            )
        )
        # A predecessor that moves under the phase is reported. The existence
        # check cannot see this — a force-pushed branch still exists — so without
        # an explicit comparison a phase would silently be based on commits
        # nobody reviewed.
        recorded_head = git(pub2, "rev-parse", first)[1]
        (pub2 / "late.txt").write_text("added to the predecessor later\n")
        git(pub2, "checkout", "-q", first)
        git(pub2, "add", "late.txt")
        git(pub2, "commit", "-q", "-m", "a late commit on the predecessor")
        moved = mode_branch(pub2, 100457, "phase-100457-dispatch-next")
        drift: dict[str, Any] = moved.get("predecessor_moved") or {}
        results.append(
            (
                "a predecessor that moved since the phase was based on it is "
                "reported, not silently accepted",
                moved["ok"]
                and drift.get("branch") == first
                and drift.get("recorded_head") == recorded_head
                and drift.get("current_head") == git(pub2, "rev-parse", first)[1]
                and "force-pushed predecessor" in (moved.get("warning") or ""),
                f"drift={drift} warning={moved.get('warning')}",
            )
        )
        # Now remove the predecessor's branch everywhere and try again.
        git(pub2, "checkout", "-q", "main")
        git(pub2, "branch", "-D", first)
        git(pub2, "push", "-q", "origin", "--delete", first)
        git(pub2, "checkout", "-q", second)
        git(pub2, "branch", "-D", second)
        refusal = mode_branch(pub2, 100458, "phase-100458-dispatch-after.md")
        results.append(
            (
                "the branch dispatch refuses when the predecessor's branch is gone, "
                "rather than falling back to the integration branch",
                (not refusal["ok"])
                and "does not exist in this checkout"
                in (refusal["repos"][0].get("blockers") or [""])[0]
                and git(pub2, "rev-parse", "--verify", f"refs/heads/{first}")[0] != 0
                and git(
                    pub2,
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{phase_branch_for(100458, 'phase-100458-dispatch-after.md')}",
                )[0]
                != 0,
                f"ok={refusal.get('ok')} "
                f"blockers={refusal.get('repos', [{}])[0].get('blockers')}",
            )
        )

        # --- The stall this whole step exists to remove. ---
        # A phase whose merge is confirmed but whose checkout cannot move:
        # the receipt says published, so nothing reports the phase as awaiting
        # review, so the old design retried nothing and the checkout stayed on a
        # dead branch for good. The tidy step is what makes it recoverable.
        pub3, nested3, _run3 = _publish_fixture(base, "dispatch-tidy", 100459)
        branch3 = "feature/dispatch-tidy"
        ensure_phase_branch(pub3, branch3, base="main")
        (pub3 / "phase.txt").write_text("the phase's change\n")
        git(pub3, "add", "phase.txt")
        git(pub3, "commit", "-q", "-m", "phase 100459: the change")
        publish_phase(pub3, 100459)
        base3 = integration_branch(pub3)
        for repo in (pub3, nested3):
            git(repo, "checkout", "-q", integration_branch(repo))
            git(repo, "merge", "-q", "--no-edit", branch3)
            git(repo, "push", "-q", "origin", integration_branch(repo))
            git(repo, "checkout", "-q", branch3)
        # One stray untracked file is enough to stop the branch switch.
        (pub3 / "stray-run-artifact.log").write_text("harness noise\n")
        stalled = mode_published(pub3, 100459)
        receipt3 = cast("dict[str, Any]", phase_receipt(pub3, 100459))
        results.append(
            (
                "a phase that is published but whose checkout cannot move reports "
                "the two outcomes separately instead of calling it still waiting",
                (not stalled["ok"])
                and stalled.get("state") == "PUBLISHED-TIDY-PENDING"
                and receipt3.get("state") == "published"
                and current_branch(pub3) == branch3
                and (pub3 / "stray-run-artifact.log").exists(),
                f"ok={stalled.get('ok')} state={stalled.get('state')} "
                f"branch={current_branch(pub3)}",
            )
        )
        # The next tick recovers, with nothing but the receipt to go on.
        (pub3 / "stray-run-artifact.log").unlink()
        tidied = mode_tidy(pub3)
        results.append(
            (
                "the checkout is recovered on a later tick from the receipt alone, "
                "so a published phase never leaves the pipeline stuck behind it",
                tidied["ok"]
                and current_branch(pub3) == base3
                and any(
                    r.get("action") == "returned-to-integration-branch"
                    for r in tidied.get("repos", [])
                ),
                f"ok={tidied.get('ok')} branch={current_branch(pub3)} "
                f"actions={[r.get('action') for r in tidied.get('repos', [])]}",
            )
        )
        # And it is idempotent, so a tick that runs it twice moves nothing.
        again = mode_tidy(pub3)
        results.append(
            (
                "tidying again is a no-op",
                again["ok"] and current_branch(pub3) == base3,
                f"branch={current_branch(pub3)}",
            )
        )

        # --- Tidy must not move a phase that is still waiting for a human. ---
        pub4, _nested4, _run4 = _publish_fixture(base, "dispatch-untidy", 100460)
        branch4 = "feature/dispatch-untidy"
        ensure_phase_branch(pub4, branch4, base="main")
        (pub4 / "phase.txt").write_text("the phase's change\n")
        git(pub4, "add", "phase.txt")
        git(pub4, "commit", "-q", "-m", "phase 100460: the change")
        publish_phase(pub4, 100460)
        # Merged, so the checkout COULD move — but this is the control: a phase
        # whose receipt still says awaiting_review must never be moved off its
        # branch, because its code is not on the integration branch yet.
        untidied = tidy_published_checkout(pub4, base=integration_branch(pub4))
        results.append(
            (
                "tidy leaves a phase that is still awaiting review exactly where it "
                "is, even though it could be moved",
                untidied["ok"]
                and untidied["action"] == "phase-not-yet-published"
                and current_branch(pub4) == branch4,
                f"action={untidied.get('action')} branch={current_branch(pub4)}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            (
                "tidy-and-dispatch checks ran to completion",
                False,
                traceback.format_exc(),
            )
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


__all__ = ["_tidy_and_dispatch_checks"]
