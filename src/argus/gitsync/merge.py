# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One sync attempt: fast-forward, or a plain merge, and the refusal that stops it."""

from pathlib import Path
from typing import Any, cast

from argus.gitsync.git import (
    FETCH_TIMEOUT_S,
    blocked,
    classify,
    dirty_paths,
    git,
    is_git_repo,
    overlapping_paths,
    resolve_upstream,
    subjects,
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
        return blocked(
            info,
            "incoming commits and local work touch the same paths",
            overlapping=clash,
            dirty=sorted(local),
            hint="commit or move the overlapping edits, then rerun; the "
            "pipeline will not merge over unreviewed work",
        )
    rc, out, err = git(repo, "merge", "--ff-only", upstream)
    if rc != 0:
        return blocked(
            info,
            f"fast-forward merge refused: {err or out}",
            dirty=sorted(local),
            overlapping=[],
            hint="git refused for a reason the overlap check does not name; "
            "read the message and resolve it by hand",
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
        return blocked(
            info,
            "incoming commits and local work touch the same paths",
            overlapping=clash,
            dirty=sorted(local),
            hint="commit or move the overlapping edits, then rerun; the "
            "pipeline will not merge over unreviewed work",
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
        return blocked(
            info,
            reason,
            conflicted=conflicts.splitlines() if conflicts else [],
            merge_error=(err or out),
            merge_aborted=aborted,
            hint=hint,
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
        return blocked(info, "not a git working tree")
    cur_branch, cur_remote, remote_branch, why = resolve_upstream(repo)
    if why:
        result = blocked(info, why)
        if cur_branch and not cur_remote:
            # The branch is checked out but has no resolvable upstream: its
            # remote-tracking ref is gone (a group branch deleted from the
            # remote, or a branch that was never pushed). Its own action names
            # that, so a reader is not sent looking at the Git destinations,
            # which are present and distinct. blocked() hardcodes
            # action="blocked"; the distinct action is the whole point, because
            # the driver and the run log both branch on it.
            result["action"] = "no-upstream"
        return result
    branch = branch or cur_branch
    remote = cast("str", remote or cur_remote)
    if branch != cur_branch:
        return blocked(info, f"checkout is on {cur_branch!r}, not {branch!r}")
    info.update(branch=branch, remote=remote)
    rc, _, err = git(repo, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return blocked(info, f"git fetch {remote} failed", detail=err)
    upstream = f"{remote}/{remote_branch}"
    rc, tip, _ = git(repo, "rev-parse", "--verify", upstream)
    if rc != 0 or not tip:
        return blocked(info, f"upstream {upstream} does not exist after fetch")
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
    return _merge(
        info, repo, upstream, ahead, behind, abort_on_conflict=(mode != "push")
    )


# The two strategies this module is split into, and the one entry point the
# sync modes and their checks call.
__all__ = ["_fast_forward", "_merge", "sync_one"]
