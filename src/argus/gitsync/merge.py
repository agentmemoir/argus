# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One sync attempt: fast-forward, or a plain merge, and the refusal that stops it."""

from pathlib import Path
from typing import Any, cast

from argus.gitsync.git import (
    FETCH_TIMEOUT_S,
    classify,
    dirty_paths,
    git,
    is_git_repo,
    overlapping_paths,
    resolve_upstream,
    subjects,
)
from argus.gitsync.report import (
    blocked,
    degraded,
    git_failure,
)


def _fast_forward(
    info: dict[str, Any], repo: str | Path, upstream: str, behind: int
) -> dict[str, Any]:
    """Integrate a strictly-behind remote by fast-forward only (no merge
    commit).

    A phase leaves the index dirty for its whole life, so a dirty index on its
    own is not a reason to stop. A fast-forward writes the incoming tree
    straight over the working tree, so git preserves a dirty index whose paths
    the incoming commits do not touch; the only thing that can go wrong is a
    path both want. Refuse that, name it, and do not ask git to try.
    """
    local = dirty_paths(repo)
    clash = overlapping_paths(repo, upstream, local)
    if clash:
        return degraded(
            info,
            "incoming commits and local work touch the same paths; "
            "the next tick retries after the phase commits or moves them",
            overlapping=clash,
            dirty=sorted(local),
        )
    rc, out, err = git(repo, "merge", "--ff-only", upstream)
    if rc != 0:
        return degraded(
            info,
            f"{git_failure('fast-forward merge', rc, err or out)}; "
            "the next tick retries",
            dirty=sorted(local),
            overlapping=[],
        )
    after = git(repo, "rev-parse", "HEAD")[1]
    return dict(
        info,
        ok=True,
        action="fast-forwarded",
        after=after,
        carried=sorted(local),
        hint=f"advanced {behind} commit(s) to {upstream}, carrying "
        f"{len(local)} uncommitted path(s) over",
    )


def _merge(
    info: dict[str, Any],
    repo: str | Path,
    upstream: str,
    ahead: int,
    behind: int,
    abort_on_conflict: bool = True,
) -> dict[str, Any]:
    """Integrate diverged histories with a plain merge commit: accept the
    remote changes while keeping every local commit. Never rebases or
    forces. On a real conflict, start mode aborts and halts; push mode
    leaves the conflict in place so the finalize agent can resolve it.

    A real merge has a stricter precondition than a fast-forward, and it is
    git's rather than ours: git refuses to merge while the index differs from
    HEAD, disjoint or not, and does so under every merge strategy. So the index
    stays a hard gate here. Unstaged and untracked work is carried through, but
    only when the incoming commits do not touch it.
    """
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        return blocked(
            info,
            "staged changes present; a merge of diverged histories "
            "needs the index to match HEAD",
            staged=staged.splitlines(),
            hint="commit or unstage the phase work, then rerun; git refuses a "
            "merge with a dirty index even when the paths are disjoint",
        )
    local = dirty_paths(repo)
    clash = overlapping_paths(repo, upstream, local)
    if clash:
        return degraded(
            info,
            "incoming commits and local work touch the same paths; "
            "the next tick retries after the phase commits or moves them",
            overlapping=clash,
            dirty=sorted(local),
        )
    rc, out, err = git(
        repo, "merge", "--no-edit", upstream, extra_env={"GIT_MERGE_AUTOEDIT": "no"}
    )
    if rc != 0:
        _, conflicts, _ = git(repo, "diff", "--name-only", "--diff-filter=U")
        aborted: bool | None = None
        if abort_on_conflict:
            _, _, ab_err = git(repo, "merge", "--abort")
            aborted = not ab_err
        reason = "clean merge impossible; remote changes conflict with local work"
        hint = "resolve by hand; the pipeline will not rebase or force"
        if not abort_on_conflict:
            hint = (
                "resolve the conflicted files, `git add` them, and "
                "complete the merge with `git commit`; never force or "
                "rebase"
            )
        return degraded(
            info,
            f"{reason}; {hint}",
            conflicted=conflicts.splitlines() if conflicts else [],
            merge_error=git_failure("merge", rc, err or out),
            merge_aborted=aborted,
        )
    after = git(repo, "rev-parse", "HEAD")[1]
    return dict(
        info,
        ok=True,
        action="merged",
        after=after,
        carried=sorted(local),
        hint=f"merged {behind} remote commit(s) with {ahead} local "
        f"commit(s); push will fast-forward",
    )


def sync_one(
    repo: str | Path,
    mode: str,
    remote: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Bring one checkout in line with its remote, accepting changes where
    that is mechanically safe.

    Fast-forwards a strictly-behind branch; merges (never rebases) a
    diverged branch and keeps every local commit. In start mode a real
    conflict, a dirty index, or a detached HEAD halts with the merge
    aborted. In push mode an ahead branch is verified as a fast-forward
    push, and a conflict is left in place for the finalize agent to
    resolve. Returns a result dict; ok is the success flag."""
    repo = Path(repo).resolve()
    info: dict[str, Any] = {"repo": str(repo), "mode": mode}
    if not is_git_repo(repo):
        return degraded(info, "not a git working tree; the next tick retries")
    cur_branch, cur_remote, remote_branch, why = resolve_upstream(repo)
    if why and cur_branch:
        # The branch may have a configured upstream whose remote-tracking ref
        # was pruned locally while the branch still exists on the remote.
        # Fetching the configured remote recreates the ref, so try once before
        # giving up; a branch the remote really deleted stays unresolved.
        rc_r, up_remote, _ = git(repo, "config", "--get", f"branch.{cur_branch}.remote")
        if rc_r == 0 and up_remote and up_remote != ".":
            fetch_rc, _, _ = git(
                repo, "fetch", "--prune", up_remote, timeout=FETCH_TIMEOUT_S
            )
            if fetch_rc == 0:
                cur_branch, cur_remote, remote_branch, why = resolve_upstream(repo)
    if why:
        result = degraded(info, why + "; the next tick retries")
        if cur_branch and not cur_remote and "has no upstream" in why:
            # The branch is checked out with no upstream at all (none
            # configured, or none left after the recovery fetch). Its own action
            # names that, so a reader is not sent looking at the Git
            # destinations, which are present and distinct. An unreadable
            # upstream keeps the generic blocked action.
            result["action"] = "no-upstream"
        return result
    branch = branch or cur_branch
    remote = cast("str", remote or cur_remote)
    if branch != cur_branch:
        return degraded(
            info,
            f"checkout is on {cur_branch!r}, not {branch!r}; the next tick retries",
        )
    info.update(branch=branch, remote=remote)
    rc, _, err = git(repo, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return degraded(
            info,
            f"git fetch {remote} failed; the next tick retries",
            detail=git_failure(f"fetch from {remote}", rc, err),
        )
    upstream = f"{remote}/{remote_branch}"
    rc, tip, _ = git(repo, "rev-parse", "--verify", upstream)
    if rc != 0 or not tip:
        return degraded(
            info, f"upstream {upstream} does not exist after fetch; retrying next tick"
        )
    local, tip, ahead, behind, relation = classify(repo, upstream)
    info.update(
        upstream=upstream,
        before=local,
        remote_tip=tip,
        ahead=ahead,
        behind=behind,
        relation=relation,
        local_only=subjects(repo, f"{upstream}..HEAD"),
        remote_only=subjects(repo, f"HEAD..{upstream}"),
    )
    if relation == "equal":
        return dict(info, ok=True, action="up-to-date", hint="local and remote match")
    if relation == "ahead":
        if mode == "push":
            return dict(
                info,
                ok=True,
                action="verified",
                hint="remote is an ancestor of local HEAD; push will fast-forward",
            )
        return dict(
            info,
            ok=True,
            action="ahead",
            hint=f"remote has nothing new; {ahead} local commit(s) "
            f"will publish at finalize",
        )
    if relation == "behind":
        return _fast_forward(info, repo, upstream, behind)
    # Always leave a conflict in place for the phase agent. Start mode used to
    # abort it, which turned a conflict the agent can fix into a halt.
    return _merge(info, repo, upstream, ahead, behind, abort_on_conflict=False)


# The two strategies this module is split into, and the one entry point the
# sync modes and their checks call.
__all__ = ["_fast_forward", "_merge", "sync_one"]
