# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase branch and branch-name checks."""

from pathlib import Path
from typing import Any

from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.git import ConfigError, current_branch, git
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.paths import assert_phase_branch_unique, phase_branch_for
from argus.gitsync.selftest_fixture import BOARD_REL, _configure_self_test, _init_remote


def _phase_branch_checks(base: Path) -> list[tuple[str, object, str]]:
    """A phase's work must never sit on the integration branch, and a branch
    must never be moved out from under a working tree that holds it."""
    results: list[tuple[str, object, str]] = []
    d = base / "branch"
    d.mkdir(parents=True)
    work: Path = _init_remote(d, "w")[1]
    name = "phase-100440-backup-archive-format-and-directory.md"
    branch = phase_branch_for(100440, name)

    # 1. Creating it carries the phase's staged work across untouched.
    (work / "c.txt").write_text("staged baseline\n")
    git(work, "add", "c.txt")
    blob: str = git(work, "rev-parse", ":c.txt")[1]
    res: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a phase branch is created from the integration branch and "
            "carries the staged baseline",
            res["ok"]
            and res["action"] == "created-phase-branch"
            and current_branch(work) == branch
            and git(work, "rev-parse", ":c.txt")[1] == blob
            and (work / "c.txt").read_text() == "staged baseline\n",
            f"branch={current_branch(work)} {res.get('action')}",
        )
    )

    # 2. Idempotent: a second tick must not move or recreate anything.
    head: str = git(work, "rev-parse", "HEAD")[1]
    res2: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "creating it again is a no-op",
            res2["ok"]
            and res2["action"] == "already-on-phase-branch"
            and git(work, "rev-parse", "HEAD")[1] == head,
            f"action={res2.get('action')}",
        )
    )

    # 3. A leftover branch from an earlier run of the same phase is resumed,
    #    not replaced, so the phase's own commits are not orphaned.
    (work / "d.txt").write_text("earlier run\n")
    git(work, "add", "d.txt")
    git(work, "commit", "-q", "-m", "earlier run of this phase")
    earlier: str = git(work, "rev-parse", "HEAD")[1]
    git(work, "checkout", "-q", "main")
    git(work, "push", "-q", "origin", "main")
    res3: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a leftover phase branch is resumed, not recreated",
            res3["ok"]
            and res3["action"] == "reused-phase-branch"
            and current_branch(work) == branch
            and git(work, "rev-parse", "HEAD")[1] == earlier,
            f"action={res3.get('action')} head={res3.get('after')}",
        )
    )

    # 4. A clean checkout on another branch is a starting point; a dirty one is
    #    the narrow lost-work stop, because the switch would reinterpret it.
    git(work, "checkout", "-q", "-b", "someone-elses-work")
    res4: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a clean checkout on another branch is switched to the phase branch",
            res4["ok"] and current_branch(work) == branch,
            f"branch={current_branch(work)} action={res4.get('action')}",
        )
    )
    git(work, "checkout", "-q", "someone-elses-work")
    (work / "unreviewed.txt").write_text("keep me\n")
    res4dirty: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a dirty checkout on another branch is not switched",
            (not res4dirty["ok"])
            and "uncommitted path" in res4dirty["blockers"][-1]
            and current_branch(work) == "someone-elses-work",
            f"blockers={res4dirty.get('blockers')}",
        )
    )
    (work / "unreviewed.txt").unlink()

    # 5. A missing integration branch is reported rather than inventing a start
    # point, and the next tick retries.
    git(work, "checkout", "-q", "main")
    res5: dict[str, Any] = ensure_phase_branch(
        work, "phase/100999-nothing-here", base="no-such-base"
    )
    results.append(
        (
            "a missing integration branch is reported and continued",
            res5["ok"] and "does not exist" in res5["warning"],
            f"warning={res5.get('warning')}",
        )
    )

    # 6. A checkout that is reviewed on the integration branch rather than as a
    #    pull request is left alone. It must not be moved onto a phase branch,
    #    because nothing there is unreviewed code, and creating the branch for
    #    it would strand run state on a branch nobody merges. A name that does
    #    not exist yet, so the check can prove nothing was created.
    fresh = "phase/100440-never-created"
    res6: dict[str, Any] = ensure_phase_branch(
        work, fresh, base="main", integration_only=True
    )
    results.append(
        (
            "an integration-branch checkout is left where it is, and no "
            "branch is created for it",
            res6["ok"]
            and res6["action"] == "stays-on-integration-branch"
            and current_branch(work) == "main"
            and git(work, "rev-parse", "--verify", f"refs/heads/{fresh}")[0] != 0,
            f"branch={current_branch(work)} {res6.get('action')}",
        )
    )

    # 7. Both cases report and continue, and the checkout is not moved: run
    #    state is still never published from an unexpected branch.
    git(work, "checkout", "-q", "-b", "unrelated-work")
    res7: dict[str, Any] = ensure_phase_branch(
        work, branch, base="main", integration_only=True
    )
    results.append(
        (
            "an integration-branch checkout on another branch is reported",
            res7["ok"]
            and "not 'main'" in res7["warning"]
            and current_branch(work) == "unrelated-work",
            f"warning={res7.get('warning')}",
        )
    )
    res8: dict[str, Any] = ensure_phase_branch(
        work, branch, base="no-such-base", integration_only=True
    )
    results.append(
        (
            "an integration-branch checkout with no base is reported",
            res8["ok"] and "does not exist" in res8["warning"],
            f"warning={res8.get('warning')}",
        )
    )
    return results


def _phase_branch_name_checks(base: Path) -> list[tuple[str, object, str]]:
    """The published branch name must not carry the phase number.

    The branch is pushed to the public repository, where the number is an
    internal label a reviewer should not have to read past. The behaviour part
    is what identifies the phase's phases file, so the number is still checked
    against the phase the caller means: that check now reads the file name,
    because the branch no longer carries the number at all.
    """
    results: list[tuple[str, object, str]] = []
    fixture = base / "branch-name"
    _configure_self_test(
        fixture, fixture / BOARD_REL, phases=Path(BOARD_REL) / "phases"
    )
    name = "phase-100440-backup-archive-format-and-directory.md"
    branch = phase_branch_for(100440, name)
    results.append(
        (
            "the branch carries the behaviour and no phase number",
            branch == "feature/backup-archive-format-and-directory"
            and "100440" not in branch,
            f"branch={branch}",
        )
    )

    # The wrong phases file must still be refused, or a phase could be launched
    # on another phase's branch.
    try:
        phase_branch_for(100841, name)
        wrong = None
    except ConfigError as exc:
        wrong = str(exc)
    results.append(
        (
            "a phases file for another phase is still refused",
            wrong is not None and "100440" in wrong and "100841" in wrong,
            f"error={wrong}",
        )
    )

    # The behaviour is the only thing separating two phases now, so a duplicate
    # must stop the launch: otherwise the second phase would resume the first
    # phase's branch and inherit its commits.
    root = fixture
    phases = root / BOARD_REL / "phases"
    phases.mkdir(parents=True)
    (phases / name).write_text("x\n")
    clash_name = "phase-100460-backup-archive-format-and-directory.md"
    (phases / clash_name).write_text("x\n")
    clash: dict[str, Any] = assert_phase_branch_unique(root, 100440, name)
    results.append(
        (
            "a behaviour another phase also claims is reported and continued",
            clash["ok"] and clash_name in str(clash.get("warning")),
            f"warning={clash.get('warning')}",
        )
    )

    (phases / clash_name).unlink()
    unique: dict[str, Any] = assert_phase_branch_unique(root, 100440, name)
    results.append(
        (
            "a behaviour only this phase claims is accepted",
            unique["ok"] and unique.get("action") == "phase-branch-name-unique",
            f"action={unique.get('action')}",
        )
    )

    # The folder the phases were chosen from is the folder that has to be
    # scanned. Reading the derived one instead would leave a collision inside a
    # configured folder unseen, which is the exact failure this check refuses.
    named = base / "branch-name-named"
    (named / BOARD_REL / "phases").mkdir(parents=True)
    elsewhere = named / "other-phases"
    elsewhere.mkdir()
    (elsewhere / name).write_text("x\n")
    (elsewhere / clash_name).write_text("x\n")
    same_name_only_in_derived: dict[str, Any] = assert_phase_branch_unique(
        named, 100440, name, elsewhere
    )
    results.append(
        (
            "a behaviour another phase in the configured folder claims is reported",
            same_name_only_in_derived["ok"]
            and clash_name in str(same_name_only_in_derived.get("warning")),
            f"warning={same_name_only_in_derived.get('warning')}",
        )
    )
    relative: dict[str, Any] = assert_phase_branch_unique(
        named, 100440, name, "other-phases"
    )
    results.append(
        (
            "a relative configured folder resolves against the repository root",
            relative["ok"] and clash_name in str(relative.get("warning")),
            f"warning={relative.get('warning')}",
        )
    )

    # And the mode itself, not only the helper it calls. The helper taking a
    # folder is worth nothing if the mode drops it on the way, and the mode is
    # the thing the driver runs on every launch. The collision is reported in
    # the mode's warnings, and the mode carries on to cut the branch.
    reported: dict[str, Any] = mode_branch(named, 100440, name, "other-phases")
    results.append(
        (
            "the branch mode scans the configured folder and reports the collision",
            reported.get("mode") == "branch"
            and clash_name in str(reported.get("warnings")),
            f"ok={reported.get('ok')} warnings={reported.get('warnings')}",
        )
    )
    return results


__all__ = ["_phase_branch_checks", "_phase_branch_name_checks"]
