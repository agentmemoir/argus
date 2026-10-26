# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run-state artifacts, and the phase-scoped commit that lands leftovers."""

from pathlib import Path
from typing import Any, cast

from argus.conventions import (
    board_repo,
    phase_run_dir,
    runs_dir,
)
from argus.conventions import (
    runstate_repo as configured_runstate_repo,
)
from argus.gitsync.git import (
    ConfigError,
    _is_private_path,
    blocked,
    degraded,
    dirty_paths,
    git,
    git_toplevel,
    is_git_repo,
    is_run_state,
    resolve_upstream,
    summarize,
)
from argus.gitsync.merge import sync_one
from argus.gitsync.runstate_artifacts import (
    ENGINE_WRITTEN_RUN_ARTIFACTS,
    is_engine_written_run_artifact,
    publish_dirt,
)


def _commit_and_push_run_state(
    repo: str | Path, message: str, pathspec: str = "runs"
) -> dict[str, Any]:
    """Commit one scoped run-state path and push without force.

    ``pathspec`` is deliberately narrow. A phase must never publish another
    phase's run directory merely because both are dirty at the same time.
    """
    branch, remote, _remote_branch, why = resolve_upstream(repo)
    info: dict[str, Any] = {"repo": str(repo), "pathspec": pathspec}
    if why:
        return degraded(info, why + "; the next tick retries")
    branch = cast("str", branch)
    remote = cast("str", remote)
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        prefix = pathspec.rstrip("/") + "/"
        outside = [
            p for p in staged.splitlines() if p != pathspec and not p.startswith(prefix)
        ]
        if outside:
            return blocked(
                info,
                "staged changes exist outside the current phase",
                staged=outside,
                hint="commit or unstage them before publishing",
            )
    scoped = _scoped_pathspecs(pathspec)
    error, committed = _stage_and_commit(repo, message, scoped)
    if error is not None:
        return degraded(info, error + "; the next tick retries")
    if not committed:
        return dict(info, ok=True, action="clean", hint=message)
    for attempt in (1, 2):
        rc, _, _ = git(repo, "push", remote, branch)
        if rc == 0:
            after = git(repo, "rev-parse", "HEAD")[1]
            return dict(
                info, ok=True, action="committed-and-pushed", after=after, hint=message
            )
        if attempt == 1:
            # The engine may have rewritten its own run artifact between the
            # commit and the push, which leaves the index staged; a real merge
            # needs a clean index. Commit that scoped path again, then integrate
            # the remote's commits (the board writes its own) before retrying.
            _stage_and_commit(repo, message, scoped)
            res = sync_one(repo, "start")
            if not res.get("ok"):
                return degraded(
                    info,
                    "remote moved and could not be integrated before pushing run state; "
                    "the next tick retries",
                    detail=res.get("summary"),
                )
    return degraded(
        info,
        f"push of run state failed (git exit {rc}); the next tick retries",
    )


def _scoped_pathspecs(pathspec: str) -> list[str]:
    """The phase path, minus the engine-written artifacts it rewrites.

    The engine rewrites its own run artifacts while the tick runs, so a commit
    that includes them can race the writer and leave the index staged against
    HEAD, which then blocks the merge that integrates the board's own commits.
    They are committed by a later, quiet cleanup instead.
    """
    return [
        pathspec,
        *(f":(exclude,glob){pathspec}/{name}" for name in ENGINE_WRITTEN_RUN_ARTIFACTS),
    ]


def _stage_and_commit(
    repo: str | Path, message: str, pathspecs: list[str]
) -> tuple[str | None, bool]:
    """Stage and commit one scoped run-state path.

    Returns ``(error, committed)``. Scoped with ``git commit -- <pathspec>``,
    so a staged path from another phase stays staged and cannot ride this
    commit. A path with nothing to commit is not an error.
    """
    rc, _, err = git(repo, "add", "-A", "--", *pathspecs)
    if rc != 0:
        return f"could not stage {pathspecs[0]} (git exit {rc})", False
    rc, out, err = git(repo, "commit", "-q", "-m", message, "--", *pathspecs)
    if rc != 0:
        combined = (err or out).lower()
        if "nothing to commit" in combined:
            return None, False
        # git's own text can name the machine, so only a fixed reason is kept.
        reason = " (no git identity)" if "identity" in combined else ""
        return f"commit of run state failed (git exit {rc}){reason}", False
    return None, True


def runstate_repo(root: str | Path, phase: int | str | None = None) -> Path | None:
    """The git working tree that owns the configured run state.

    The reservation board and the run state may live in different repositories.
    A ``runstate.repo`` setting names the checkout directly. Otherwise it is the
    working tree that holds the run directory, which ``driver.runs`` names
    through the pipeline; ``board.repo`` no longer has to be that repository. A
    layout with no working tree for the run state falls back to the board
    checkout, which is the only repository run state had before the two could be
    separated.
    """
    configured = configured_runstate_repo(root)
    if configured is not None:
        return configured
    try:
        run_dir = runs_dir(root) if phase is None else phase_run_dir(root, int(phase))
    except (TypeError, ValueError):
        return board_repo(root)
    return git_toplevel(run_dir) or board_repo(root)


def _phase_run_pathspec(phase: int | str, root: str | Path | None = None) -> str:
    root = Path(root or Path.cwd()).resolve()
    try:
        number = int(phase)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid phase number: {phase!r}") from None
    if number < 0 or number > 999999:
        raise ConfigError(f"phase number out of range: {number}")
    try:
        resolved = phase_run_dir(root, number).resolve()
    except ValueError as exc:
        # The run directory could not be computed at all, most often because the
        # phase file the pipeline names is gone. This is a different failure from
        # a directory that is not in a checkout, and reporting it as one sent a
        # reader looking at paths when the input was missing.
        raise ConfigError(
            f"cannot locate the run directory for phase {number}: {exc}"
        ) from exc
    repo = runstate_repo(root, number)
    if repo is None:
        raise ConfigError(
            f"phase {number} run directory is not inside a git working tree to publish"
        )
    try:
        return str(resolved.relative_to(repo))
    except ValueError as exc:
        raise ConfigError(
            "phase run directory must be inside its run-state checkout to publish"
        ) from exc


def commit_leftover_run_state(
    root: str | Path, message: str, phase: int | None = None
) -> dict[str, Any]:
    """Commit only the current phase's private run state.

    With ``phase`` omitted, the legacy all-``runs`` scope remains available
    for standalone maintenance. The pipeline always supplies a phase so a
    concurrent phase's partial records cannot be swept into this publication.
    """
    root = Path(root).resolve()
    repo = runstate_repo(root, phase)
    if repo is None or not is_git_repo(repo):
        # Run state must land in a git checkout. Treating an unresolvable or
        # non-repository checkout as an empty run reported success while the
        # state stayed dirty, which the next phase's publish refused on.
        blockers = [
            blocked(
                {"repo": str(root)},
                "no run-state checkout for the configured run state"
                if repo is None
                else f"the run-state checkout is not a git working tree: {repo}",
                hint="set runstate.repo or place driver.runs inside a git checkout",
            )
        ]
        return {
            "ok": False,
            "mode": "start-clean",
            "repos": blockers,
            "pathspec": "runs",
            "summary": summarize(blockers, "start-clean"),
        }
    if phase is not None:
        pathspec = _phase_run_pathspec(phase, root)
    else:
        try:
            pathspec = str(runs_dir(root).resolve().relative_to(repo))
        except ValueError:
            pathspec = "runs"
    results: list[dict[str, Any]] = []
    # The nested private checkout is a separate repo by design; if a host
    # lacks the global ignore for it, do not mistake it for public dirt.
    pub_dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
    # When the run state lives in the public checkout itself, its own paths are
    # the change this helper is about to commit, not public dirt that blocks it.
    if repo == root:
        pub_dirty = [p for p in pub_dirty if not is_run_state(p, pathspec)]
    if pub_dirty:
        results.append(
            blocked(
                {"repo": str(root)},
                "public checkout has changes outside the pipeline's run state",
                dirty=pub_dirty,
                hint="commit or revert them, then rerun",
            )
        )
    if is_git_repo(repo):
        paths = dirty_paths(repo)
        if phase is None:
            outside = [p for p in paths if not is_run_state(p, pathspec)]
            inside = [p for p in paths if is_run_state(p, pathspec)]
        else:
            prefix = pathspec + "/"
            # Unstaged files from another phase are deliberately left alone;
            # only staged paths outside this phase are dangerous because a
            # commit would carry them accidentally.
            _, staged, _ = git(repo, "diff", "--cached", "--name-only")
            outside = [
                p
                for p in staged.splitlines()
                if p != pathspec and not p.startswith(prefix)
            ]
            inside = [p for p in paths if p == pathspec or p.startswith(prefix)]
        if outside:
            if phase is not None:
                # Narrow lost-work stop: staged changes outside the phase are
                # never carried, and this publication waits until they are moved.
                results.append(
                    blocked(
                        {"repo": str(repo)},
                        "private checkout has changes outside the current phase",
                        dirty=outside,
                        hint="commit or move them before publishing this phase",
                    )
                )
            else:
                # The sweep is scoped per phase, so a foreign staged path cannot
                # ride it. Report it and keep going.
                results.append(
                    degraded(
                        {"repo": str(repo)},
                        "private checkout has changes outside runs/; they stay "
                        "staged and are reported, the next tick retries",
                        dirty=outside,
                    )
                )
        if inside:
            results.append(_commit_and_push_run_state(repo, message, pathspec=pathspec))
        elif not outside:
            results.append({"repo": str(repo), "ok": True, "action": "clean"})
    ok = all(r.get("ok") for r in results)
    return {
        "ok": ok,
        "mode": "start-clean",
        "repos": results,
        "pathspec": pathspec,
        "summary": summarize(results, "start-clean"),
    }


# The run-state helpers the publication, the cleanup, and their checks call.
__all__ = [
    "_commit_and_push_run_state",
    "_phase_run_pathspec",
    "commit_leftover_run_state",
    "is_engine_written_run_artifact",
    "publish_dirt",
    "runstate_repo",
]
