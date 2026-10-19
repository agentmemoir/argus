# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Both checkouts synced together, and the integration branch merged into a phase branch."""

from pathlib import Path
from typing import Any

from argus.conventions import board_repo, phases_repo
from argus.gitsync.git import (
    FETCH_TIMEOUT_S,
    blocked,
    classify,
    configured_remote_urls,
    current_branch,
    degraded,
    git,
    integration_branch,
    is_git_repo,
    resolve_upstream,
    summarize,
)
from argus.gitsync.merge import _fast_forward, _merge, sync_one
from argus.gitsync.runstate import runstate_repo


def resync_integration(repo: str | Path, base: str | None = None) -> dict[str, Any]:
    """Merge the integration branch into the checkout's current phase branch.

    The gap this closes: nothing pulls the integration branch into a running
    phase. The pre-launch sync brings the *current* branch in line with its own
    remote, and the publish-time sync compares the phase branch against its own
    upstream, so a master that moves during the phase is invisible until a
    reviewer sees a stale diff.

    It reuses the two integration primitives rather than adding a third. The
    classification is the same ``classify`` the normal sync uses, so a phase
    behind the integration branch fast-forwards and a diverged one takes a
    plain merge commit, never a rebase.

    The conflict is left in place, not aborted, and that is the whole point: a
    conflict the pipeline cleans up is a conflict nobody fixes, and a phase that
    cannot resolve one cannot be handed back to the agent either. The finalize
    stage already has the instruction to finish such a merge by hand, and
    ``01-implement.md`` gains the same one.

    A checkout already on the integration branch is a no-op, which is what the
    private checkout always is.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info: dict[str, Any] = {"repo": str(repo), "mode": "resync", "base": base}
    if not is_git_repo(repo):
        # Narrow own-state stop: a lost checkout refuses rather than letting a
        # git call walk up into the enclosing repository.
        return blocked(
            info, "not a git working tree; restore the checkout's repository"
        )
    here = current_branch(repo)
    if not here:
        return degraded(
            info,
            "detached HEAD; the resync is retried once the phase branch is checked out",
        )
    info["branch"] = here
    if here == base:
        return dict(
            info,
            ok=True,
            action="already-on-integration-branch",
            hint=f"already on {base}; there is no phase branch to resync",
        )
    # The remote is read from the phase branch first, and from the base branch
    # second, because a phase branch that has never been pushed has no upstream
    # of its own and the sync helper refuses a branch with no remote copy.
    _branch, remote, _rb, why = resolve_upstream(repo)
    if not remote:
        rc_r, from_base, _ = git(repo, "config", "--get", f"branch.{base}.remote")
        remote = from_base if rc_r == 0 else None
    if not remote:
        return degraded(
            info,
            (why or "no remote is configured") + "; the next tick retries the resync",
        )
    rc, _, err = git(repo, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return degraded(
            info, f"git fetch {remote} failed; the next tick retries", detail=err
        )
    upstream = f"{remote}/{base}"
    rc, _, _ = git(repo, "rev-parse", "--verify", upstream)
    if rc != 0:
        return degraded(
            info,
            f"{upstream} does not exist after fetch; the next tick retries",
        )
    local, tip, ahead, behind, relation = classify(repo, upstream)
    info.update(
        upstream=upstream,
        before=local,
        remote_tip=tip,
        ahead=ahead,
        behind=behind,
        relation=relation,
    )
    if relation == "equal":
        return dict(
            info,
            ok=True,
            action="up-to-date",
            hint=f"{here} already contains {upstream}",
        )
    if relation == "behind":
        return _fast_forward(info, repo, upstream, behind)
    if relation == "ahead":
        # The phase branch has commits the integration branch does not, and the
        # integration branch has nothing new. Merging would be a no-op with a
        # commit attached, so it is reported and left alone.
        return dict(
            info,
            ok=True,
            action="phase-ahead",
            hint=f"{base} has nothing new; {ahead} phase commit(s) "
            "stay local until finalize pushes them",
        )
    # A diverged phase branch is the only case that needs a real merge, and a
    # real merge is the only case git refuses while the index is dirty. That is
    # reported under its own action with the staged paths, because "the merge
    # could not happen" and "the merge would have conflicted" need different
    # responses from whoever reads it, and _merge's single blocked result
    # collapses them into one line that reads the same either way.
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        blocked_result = blocked(
            info,
            "the index holds staged changes, and git refuses to merge "
            "diverged histories while the index differs from HEAD",
            staged=staged.splitlines(),
            hint="run this again once the index is clean — after committing, "
            "or from a tree with nothing staged. This is git's rule, not "
            "this helper's, so there is no flag that bypasses it",
        )
        # blocked() hardcodes action="blocked"; the distinct action is the whole
        # point, because the driver and the run log both branch on it.
        blocked_result["action"] = "resync-blocked-dirty-index"
        return blocked_result
    return _merge(info, repo, upstream, ahead, behind, abort_on_conflict=False)


def _shared_destination(checkouts: dict[str, Path]) -> str | None:
    """A reason the sync must refuse, or None when it can go ahead.

    Only the public checkout must differ from the private ones. The board and
    the phases may share one destination among themselves, so they are never
    compared with each other. A checkout missing a fetch or push URL still
    fails closed.
    """
    public_fetch = configured_remote_urls(checkouts["public"])
    public_push = configured_remote_urls(checkouts["public"], push=True)
    if not public_fetch or not public_push:
        return (
            "cannot prove separate Git destinations for the public "
            "repository; refusing to sync"
        )
    for role, path in checkouts.items():
        if role == "public":
            continue
        fetch = configured_remote_urls(path)
        push = configured_remote_urls(path, push=True)
        if not fetch or not push:
            return (
                f"cannot prove separate Git destinations for the {role} "
                "repository; refusing to sync"
            )
        if public_push & push or public_push & fetch or public_fetch & push:
            return (
                f"public and {role} repositories share a Git push/fetch "
                "destination; refusing to sync"
            )
    return None


def sync_all(root: str | Path, mode: str, branch: str | None = None) -> dict[str, Any]:
    """Sync the public checkout, the board, the phases, and the run state, if
    present.

    ``branch`` is the branch the public checkout must be on, and it reaches that
    checkout alone: the board and the phases are read on their own branches, so
    naming a group branch for them would refuse them for no reason. A caller
    that names no branch leaves the check comparing a checkout's own branch
    against itself, which is what every caller did before a group could name a
    branch at all.
    """
    root = Path(root).resolve()
    checkouts: dict[str, Path] = {"public": root}
    nested = board_repo(root)
    if nested is not None and is_git_repo(nested):
        checkouts["board"] = nested
    phases = phases_repo(root)
    if phases is not None and phases not in checkouts.values() and is_git_repo(phases):
        checkouts["phases"] = phases
    runstate = runstate_repo(root)
    if (
        runstate is not None
        and runstate not in checkouts.values()
        and is_git_repo(runstate)
    ):
        checkouts["runstate"] = runstate
    if len(checkouts) > 1:
        reason = _shared_destination(checkouts)
        if reason is not None:
            results = [
                blocked({"repo": str(path), "mode": mode}, reason)
                for path in checkouts.values()
            ]
            return {
                "ok": False,
                "mode": mode,
                "repos": results,
                "summary": summarize(results, mode),
            }
    repos = list(checkouts.values())
    results = [sync_one(repos[0], mode, branch=branch)]
    results += [sync_one(r, mode) for r in repos[1:]]
    ok = all(r["ok"] for r in results)
    return {
        "ok": ok,
        "mode": mode,
        "repos": results,
        "summary": summarize(results, mode),
    }


def mode_resync(root: str | Path) -> dict[str, Any]:
    """Merge the integration branch into every checkout's current branch."""
    root = Path(root).resolve()
    # The public checkout is the one that can be on a phase branch. The private
    # one is always on the integration branch and reports itself as a no-op,
    # which is named rather than dropped so the two stay distinguishable in the
    # run record.
    nested = board_repo(root)
    targets = [root]
    if nested is not None and is_git_repo(nested):
        targets.append(nested)
    phases = phases_repo(root)
    if phases is not None and phases not in targets and is_git_repo(phases):
        targets.append(phases)
    runstate = runstate_repo(root)
    if runstate is not None and runstate not in targets and is_git_repo(runstate):
        targets.append(runstate)
    repos = [resync_integration(r) for r in targets]
    ok = all(r["ok"] for r in repos)
    return {
        "ok": ok,
        "mode": "resync",
        "repos": repos,
        "summary": summarize(repos, "resync"),
    }
