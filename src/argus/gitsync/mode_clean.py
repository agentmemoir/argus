# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The clean and checkpoint modes: leftover run state, and work in progress."""

from pathlib import Path
from typing import Any, cast

from argus.conventions import board_repo
from argus.gitsync.git import (
    ConfigError,
    blocked,
    dirty_paths,
    git,
    integration_branch,
    is_git_repo,
    resolve_upstream,
    summarize,
)
from argus.gitsync.modes import _run_phase_numbers
from argus.gitsync.runstate import _commit_and_push_run_state, _phase_run_pathspec


def mode_clean(root: str | Path, phase: int | str | None = None) -> dict[str, Any]:
    """Commit and push one phase's leftover private run state, and only that.

    The private-only sibling of ``commit_leftover_run_state``. It runs after a
    phase's own run has finished, when the runner has written the run state that
    lands after finalize's publish commit: the ledger's finalize row, the run
    marker, the usage files, the deleted resume record, and the finalize log.
    Those writes were never committed, so the next phase's publish found them
    dirty, refused with ``working tree is dirty after phase cleanup`` and halted
    the line.

    It does not refuse because the public checkout is dirty, and that is the
    whole difference. A phase's closing run-state writes belong to the private
    repository, and the public checkout is legitimately mid-work while the phase
    it just finished sits on its own branch. The pathspec keeps the phase scope
    the publish helper already relies on: this never stages another phase's run
    state.

    With ``phase`` omitted, every phase's leftover run state is committed, each
    scoped to its own directory. This is the fresh-start sweep: a phase whose run
    ended without its own closing commit — an interruption, a failed clean, or a
    run from before the cleanup existed — leaves writes the next phase's publish
    refuses on, and this lands them before that launch. Each directory stays its
    own pathspec, so a later phase's partial records are still never swept into
    an earlier phase's commit.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    if private is None or not is_git_repo(private):
        return {
            "ok": True,
            "mode": "clean",
            "repos": [],
            "pathspec": "runs",
            "summary": f"clean sync ok: no private checkout at {private}",
        }
    if phase is None:
        # Only phases with leftover dirt are committed. A phase already clean
        # would make `git commit` report "no changes added to commit" while a
        # sibling phase is still dirty, which the helper reads as a failure.
        dirty = set(dirty_paths(private))
        numbers: list[int] = []
        for number in _run_phase_numbers(root):
            try:
                pathspec = _phase_run_pathspec(number, root)
            except ConfigError:
                # A run directory whose phase file is gone cannot be scoped to a
                # pathspec, so this sweep cannot land it. Skipping it keeps one
                # stale directory from stopping the sweep for every other phase,
                # which is the only reason the sweep exists: the next phase's
                # publish otherwise refuses on run state nobody landed.
                continue
            prefix = pathspec + "/"
            if any(p == pathspec or p.startswith(prefix) for p in dirty):
                numbers.append(number)
        if not numbers:
            return {
                "ok": True,
                "mode": "clean",
                "repos": [],
                "pathspec": "runs",
                "summary": "clean sync ok: no leftover run state",
            }
        results = [
            _commit_and_push_run_state(
                private,
                f"runs: close out phase {number:06d} run state",
                pathspec=_phase_run_pathspec(number, root),
            )
            for number in numbers
        ]
        ok = all(res.get("ok") for res in results)
        return {
            "ok": ok,
            "mode": "clean",
            "repos": results,
            "pathspec": "runs",
            "summary": summarize(results, "clean"),
        }
    pathspec = _phase_run_pathspec(phase, root)
    res = _commit_and_push_run_state(
        private, f"runs: close out phase {int(phase):06d} run state", pathspec=pathspec
    )
    return {
        "ok": bool(res.get("ok")),
        "mode": "clean",
        "repos": [res],
        "pathspec": pathspec,
        "summary": summarize([res], "clean"),
    }


def mode_checkpoint(root: str | Path) -> dict[str, Any]:
    """Commit and push a checkout's work in progress on the branch it is on.

    This is the handoff half of a release. The owner is giving the reservation
    back, so the work it did has to reach the branch before the next owner picks
    the group up; otherwise the handoff is a restart. The integration branch is
    refused, because pushing unfinished product code onto the branch everyone
    pulls is the thing the phase branch exists to prevent. A checkout with
    nothing to commit is a no-op, not a failure, so an owner with nothing to push
    still releases.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    info = {"repo": str(root)}
    if not is_git_repo(root):
        return dict(
            info, ok=True, action="checkpoint-skipped", hint="not a git working tree"
        )
    branch, remote, _remote_branch, why = resolve_upstream(root)
    if why:
        return blocked(info, why)
    remote = cast("str", remote)
    branch = cast("str", branch)
    if branch == integration_branch(root):
        return blocked(
            info,
            f"refusing to push work in progress on {branch!r}",
            hint="a handoff is the phase branch, not the integration branch",
        )
    rc, out, err = git(root, "add", "-A")
    if rc != 0:
        return blocked(info, f"could not stage the checkout: {err or out}")
    # Ask git first. With nothing staged, the pre-commit hook refused the commit
    # with its own text, and git's "nothing to commit" never appeared. So an
    # empty stage must not reach the commit.
    rc, out, err = git(root, "diff", "--cached", "--quiet")
    if rc not in (0, 1):
        return blocked(info, f"could not read the staged changes: {err or out}")
    action = "pushed"
    if rc == 1:
        rc, out, err = git(
            root, "commit", "-q", "-m", f"checkpoint: work in progress on {branch}"
        )
        if rc != 0:
            return blocked(info, f"checkpoint commit failed: {err or out}")
        action = "committed-and-pushed"
    rc, out, err = git(root, "push", remote, branch)
    if rc != 0:
        return blocked(info, f"checkpoint push failed: {err or out}")
    head = git(root, "rev-parse", "HEAD")[1]
    return dict(info, ok=True, action=action, branch=branch, head=head)
