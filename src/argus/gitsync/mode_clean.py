# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The clean and checkpoint modes: leftover run state, and work in progress."""

from pathlib import Path
from typing import Any, cast

from argus.gitsync.fence import phase_fence
from argus.gitsync.git import (
    ConfigError,
    degraded,
    dirty_paths,
    git,
    integration_branch,
    is_git_repo,
    push_moved,
    resolve_upstream,
    summarize,
)
from argus.gitsync.modes import _run_phase_numbers
from argus.gitsync.receipts import phase_receipt
from argus.gitsync.runstate import (
    _commit_and_push_run_state,
    _phase_run_pathspec,
    runstate_repo,
)


def _clean_refusal(refusal: dict[str, Any], pathspec: str) -> dict[str, Any]:
    """The mode's own shape around a fence refusal, so a caller sees one contract."""
    return {
        "ok": False,
        "mode": "clean",
        "state": refusal["state"],
        "error": refusal["error"],
        "phase": refusal["phase"],
        "repos": [],
        "pathspec": pathspec,
        "summary": (
            f"clean sync blocked: the {refusal['state']} fence refused phase "
            f"{refusal['phase']}: {refusal['error']}"
        ),
    }


def _sweep_one(
    root: Path, repo: Path, number: int, pathspec: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Commit one phase's leftovers, or name why the sweep skips it.

    A phase whose receipt is terminal is exempt by construction: its run is
    recorded as published, nothing the sweep lands can change that verdict, and
    a released claim is the ordinary end state rather than a lost fence.

    Every other phase must prove the fence first. A phase that cannot is
    skipped, not fatal: a hard fail would strand post-merge run state that the
    next phase's publish then refuses on, which is the stall the sweep exists to
    end. The skip is named in the result, so it is visible rather than silent.
    """
    receipt = phase_receipt(root, number)
    terminal = isinstance(receipt, dict) and receipt.get("state") == "published"
    if not terminal:
        refusal = phase_fence(root, number, "clean")
        if refusal is not None:
            return None, {
                "phase": f"{number:06d}",
                "state": refusal["state"],
                "error": refusal["error"],
                "detail": (
                    f"skipped: the {refusal['state']} fence refused phase "
                    f"{number:06d}, so its leftover run state was not landed"
                ),
            }
    return (
        _commit_and_push_run_state(
            repo,
            f"runs: close out phase {number:06d} run state",
            pathspec=pathspec,
        ),
        None,
    )


def mode_clean(root: str | Path, phase: int | str | None = None) -> dict[str, Any]:
    """Commit and push one phase's leftover private run state, and only that.

    The private-only sibling of ``commit_leftover_run_state``. It runs after a
    phase's own run has finished, when the runner has written the run state that
    lands after finalize's publish commit: the ledger's finalize row, the run
    marker, the usage files, the deleted resume record, and the finalize log.
    Those writes were never committed, so the next phase's publish found them
    dirty, refused with ``working tree is dirty after phase cleanup`` and halted
    the line.

    The board fence is read before the commit: the commit and its push are the
    first writes on this path, and a machine whose claim expired or was taken
    over must not land run state under another owner's phase. A fence that does
    not hold writes nothing.

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

    The sweep has no single owner, so it fences per planned phase: a phase with
    a terminal receipt is exempt, a phase whose fence holds is landed, and a
    phase whose fence does not hold is skipped with its reason in ``skipped``.
    One unproven phase never stops the others.
    """
    root = Path(root).resolve()
    if phase is None:
        # Only phases with leftover dirt are committed. A phase already clean
        # would make `git commit` report "no changes added to commit" while a
        # sibling phase is still dirty, which the helper reads as a failure.
        # Each phase is scoped to the checkout that holds its own run directory,
        # so a run directory outside the configured run folder still lands.
        planned: list[tuple[Path, int, str]] = []
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
            repo = runstate_repo(root, number)
            if repo is None or not is_git_repo(repo):
                continue
            prefix = pathspec + "/"
            dirty = set(dirty_paths(repo))
            if any(p == pathspec or p.startswith(prefix) for p in dirty):
                planned.append((repo, number, pathspec))
        if not planned:
            return {
                "ok": True,
                "mode": "clean",
                "repos": [],
                "pathspec": "runs",
                "summary": "clean sync ok: no leftover run state",
            }
        results: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for repo, number, pathspec in planned:
            landed, held = _sweep_one(root, repo, number, pathspec)
            if landed is not None:
                results.append(landed)
            if held is not None:
                skipped.append(held)
        # A sweep that landed nothing left every phase's leftovers dirty, so an
        # empty result is a failure, not a vacuous success.
        ok = bool(results) and all(res.get("ok") for res in results)
        out: dict[str, Any] = {
            "ok": ok,
            "mode": "clean",
            "repos": results,
            "pathspec": "runs",
            "summary": summarize(results, "clean"),
        }
        if skipped:
            out["skipped"] = skipped
            out["state"] = "PARTIAL"
            out["summary"] = (
                f"{out['summary']}; {len(skipped)} phase(s) skipped for an "
                "unproven fence: "
                + ", ".join(
                    f"{one['phase']} ({one['state']}: {one['error']})"
                    for one in skipped
                )
            )
        return out
    pathspec = _phase_run_pathspec(phase, root)
    refusal = phase_fence(root, phase, "clean")
    if refusal is not None:
        return _clean_refusal(refusal, pathspec)
    repo = runstate_repo(root, phase)
    if repo is None or not is_git_repo(repo):
        return {
            "ok": True,
            "mode": "clean",
            "repos": [],
            "pathspec": "runs",
            "summary": f"clean sync ok: no private checkout at {repo}",
        }
    res = _commit_and_push_run_state(
        repo, f"runs: close out phase {int(phase):06d} run state", pathspec=pathspec
    )
    return {
        "ok": bool(res.get("ok")),
        "mode": "clean",
        "repos": [res],
        "pathspec": pathspec,
        "summary": summarize([res], "clean"),
    }


def mode_checkpoint(
    root: str | Path, paths: list[str] | None = None, phase: int | str | None = None
) -> dict[str, Any]:
    """Commit and push a checkout's work in progress on the branch it is on.

    This is the handoff half of a release. The owner is giving the reservation
    back, so the work it did has to reach the branch before the next owner picks
    the group up; otherwise the handoff is a restart. The integration branch is
    refused, because pushing unfinished product code onto the branch everyone
    pulls is the thing the phase branch exists to prevent. A checkout with
    nothing to commit is a no-op, not a failure, so an owner with nothing to push
    still releases; the action names what the push did, ``pushed`` when the
    branch moved without a new commit and ``nothing-to-push`` when it did not.

    ``phase`` names the reservation the work belongs to, and the fence is read
    before anything is staged or committed. A checkpoint written on an expired
    or taken-over claim would push another owner's work onto the branch the next
    owner resumes from. Without a phase there is no reservation to prove, so the
    fence is the caller's: ``release`` has already read it before it calls here.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    if phase is not None:
        refusal = phase_fence(root, phase, "checkpoint")
        if refusal is not None:
            return refusal
    info: dict[str, Any] = {"repo": str(root)}
    if not is_git_repo(root):
        return dict(
            info, ok=True, action="checkpoint-skipped", hint="not a git working tree"
        )
    branch, remote, _remote_branch, why = resolve_upstream(root)
    if why:
        return degraded(info, why + "; the checkpoint is retried next tick")
    remote = cast("str", remote)
    branch = cast("str", branch)
    if branch == integration_branch(root):
        return degraded(
            info,
            f"work in progress stays local on {branch!r}; a handoff needs a "
            "phase branch, so the checkpoint is retried after the branch cut",
        )
    if paths:
        rc, staged, err = git(
            root, "diff", "--cached", "--no-renames", "--name-only", "-z"
        )
        if rc:
            return degraded(info, "could not read staged paths; retrying next tick")
        rc, scoped, err = git(
            root, "diff", "--cached", "--no-renames", "--name-only", "-z", "--", *paths
        )
        if rc:
            return degraded(
                info, "could not read scoped staged paths; retrying next tick"
            )
        outside = sorted(set(staged.split("\0")) - set(scoped.split("\0")) - {""})
        if outside:
            return degraded(
                info,
                "staged changes exist outside checkpoint paths; they stay "
                "staged and the checkpoint is retried",
                staged=outside,
            )
        rc, selected, err = git(
            root,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *paths,
        )
        if rc:
            return degraded(info, "could not read checkpoint paths; retrying next tick")
        info["left_uncommitted"] = sorted(
            set(dirty_paths(root)) - set(selected.split("\0"))
        )
    rc, out, err = git(root, "add", "-A", *(["--", *paths] if paths else []))
    if rc != 0:
        return degraded(
            info, f"could not stage the checkout: {err or out}; retrying next tick"
        )
    # Ask git first. With nothing staged, the pre-commit hook refused the commit
    # with its own text, and git's "nothing to commit" never appeared. So an
    # empty stage must not reach the commit, and the push still runs: a repair
    # commits before this checkpoint, so an empty stage can carry a commit the
    # branch does not have yet, and the push is what says whether it moved.
    rc, out, err = git(root, "diff", "--cached", "--quiet")
    if rc not in (0, 1):
        return degraded(
            info,
            f"could not read the staged changes: {err or out}; retrying next tick",
        )
    committed = rc == 1
    if committed:
        rc, out, err = git(
            root,
            "commit",
            "-q",
            "-m",
            f"{'wip' if paths else 'checkpoint'}: work in progress on {branch}",
        )
        if rc != 0:
            return degraded(
                info,
                f"checkpoint commit failed: {err or out}; retrying next tick",
            )
    # --porcelain reports a ref that was already up to date as well, so the
    # result names what the push did instead of what the stage held.
    rc, out, err = git(root, "push", "--porcelain", remote, branch)
    if rc != 0:
        return degraded(
            info, f"checkpoint push failed: {err or out}; retrying next tick"
        )
    if committed:
        action = "committed-and-pushed"
    elif push_moved(out, branch):
        action = "pushed"
    else:
        action = "nothing-to-push"
    head = git(root, "rev-parse", "HEAD")[1]
    return dict(info, ok=True, action=action, branch=branch, head=head)
