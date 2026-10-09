# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
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

    # 4. A checkout sitting on some other branch is a hard stop. Moving a
    #    working tree that holds unreviewed work between branches is the
    #    operation that can leave that work in pieces.
    git(work, "checkout", "-q", "-b", "someone-elses-work")
    res4: dict[str, Any] = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a checkout on another branch is never switched silently",
            (not res4["ok"])
            and "not the phase branch" in res4["blockers"][-1]
            and current_branch(work) == "someone-elses-work",
            f"blockers={res4.get('blockers')}",
        )
    )

    # 5. A missing integration branch stops rather than inventing a start point.
    git(work, "checkout", "-q", "main")
    res5: dict[str, Any] = ensure_phase_branch(
        work, "phase/100999-nothing-here", base="no-such-base"
    )
    results.append(
        (
            "a missing integration branch refuses",
            (not res5["ok"]) and "does not exist" in res5["blockers"][-1],
            f"blockers={res5.get('blockers')}",
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

    # 7. It is still fail-closed: a checkout sitting on some other branch is a
    #    stop, and a missing integration branch is a stop. Silence here would
    #    let run state be published from an unexpected place.
    git(work, "checkout", "-q", "-b", "unrelated-work")
    res7: dict[str, Any] = ensure_phase_branch(
        work, branch, base="main", integration_only=True
    )
    results.append(
        (
            "an integration-branch checkout on another branch refuses",
            (not res7["ok"])
            and "not 'main'" in res7["blockers"][-1]
            and current_branch(work) == "unrelated-work",
            f"blockers={res7.get('blockers')}",
        )
    )
    res8: dict[str, Any] = ensure_phase_branch(
        work, branch, base="no-such-base", integration_only=True
    )
    results.append(
        (
            "an integration-branch checkout with no base refuses",
            (not res8["ok"]) and "does not exist" in res8["blockers"][-1],
            f"blockers={res8.get('blockers')}",
        )
    )
    return results


def _phase_branch_name_checks(base: Path) -> list[tuple[str, object, str]]:
    """The published branch name must not carry the phase number.

    The branch is pushed to the public repository, where the number is an
    internal label a reviewer should not have to read past. The behaviour part
    is what identifies the phase's roadmap file, so the number is still checked
    against the phase the caller means: that check now reads the file name,
    because the branch no longer carries the number at all.
    """
    results: list[tuple[str, object, str]] = []
    fixture = base / "branch-name"
    _configure_self_test(
        fixture, fixture / BOARD_REL, roadmap=Path(BOARD_REL) / "roadmap"
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

    # The wrong roadmap file must still be refused, or a phase could be launched
    # on another phase's branch.
    try:
        phase_branch_for(100841, name)
        wrong = None
    except ConfigError as exc:
        wrong = str(exc)
    results.append(
        (
            "a roadmap file for another phase is still refused",
            wrong is not None and "100440" in wrong and "100841" in wrong,
            f"error={wrong}",
        )
    )

    # The behaviour is the only thing separating two phases now, so a duplicate
    # must stop the launch: otherwise the second phase would resume the first
    # phase's branch and inherit its commits.
    root = fixture
    roadmap = root / BOARD_REL / "roadmap"
    roadmap.mkdir(parents=True)
    (roadmap / name).write_text("x\n")
    clash_name = "phase-100460-backup-archive-format-and-directory.md"
    (roadmap / clash_name).write_text("x\n")
    clash: dict[str, Any] = assert_phase_branch_unique(root, 100440, name)
    results.append(
        (
            "a behaviour another phase also claims is refused",
            (not clash["ok"]) and clash_name in clash["blockers"][-1],
            f"blockers={clash.get('blockers')}",
        )
    )

    (roadmap / clash_name).unlink()
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
    (named / BOARD_REL / "roadmap").mkdir(parents=True)
    elsewhere = named / "other-roadmap"
    elsewhere.mkdir()
    (elsewhere / name).write_text("x\n")
    (elsewhere / clash_name).write_text("x\n")
    same_name_only_in_derived: dict[str, Any] = assert_phase_branch_unique(
        named, 100440, name, elsewhere
    )
    results.append(
        (
            "a behaviour another phase in the configured folder claims is refused",
            (not same_name_only_in_derived["ok"])
            and clash_name in same_name_only_in_derived["blockers"][-1],
            f"blockers={same_name_only_in_derived.get('blockers')}",
        )
    )
    relative: dict[str, Any] = assert_phase_branch_unique(
        named, 100440, name, "other-roadmap"
    )
    results.append(
        (
            "a relative configured folder resolves against the repository root",
            (not relative["ok"]) and clash_name in relative["blockers"][-1],
            f"blockers={relative.get('blockers')}",
        )
    )

    # And the mode itself, not only the helper it calls. The helper taking a
    # folder is worth nothing if the mode drops it on the way, and the mode is
    # the thing the driver runs on every launch. This fixture has no git
    # remotes, so the check is about the refusal the mode returns: a mode that
    # reached the uniqueness check at all refuses, and one that dropped the
    # folder would carry on to try to cut a branch in a directory that is not a
    # repository, which is a different failure.
    refused: dict[str, Any] = mode_branch(named, 100440, name, "other-roadmap")
    results.append(
        (
            "the branch mode scans the configured folder and refuses the collision",
            (not refused["ok"])
            and refused.get("mode") == "branch"
            and clash_name in str(refused.get("repos")),
            f"ok={refused.get('ok')} blockers={refused.get('repos')}",
        )
    )
    return results


__all__ = ["_phase_branch_checks", "_phase_branch_name_checks"]
