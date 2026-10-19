# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run-state artifacts, and the phase-scoped commit that lands leftovers."""

import re
from collections.abc import Iterable
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

# Run artifacts the running engine rewrites in place while a step runs.
#
# The test is the property, not the filename: a file sitting directly in a
# phase's run directory whose whole content is a timestamp or an engine audit
# line. The engine that is publishing is the engine that writes them, at least
# once a second, so a check that demands a clean tree races the writer it is
# checking and cannot win. The publication helper used to be unpublishable
# from inside the engine for exactly that reason.
#
# These files are still committed and still pushed. The exemption is only
# about the *check* that runs after the commit, and it is deliberately narrow:
# a run log, findings, a ledger, a resume record, a receipt, a modified source
# file, a staged change and an untracked file all still refuse publication.
# Both halves of the test matter: the name alone would exempt a source file
# that happens to be called `activity`, and the directory alone would exempt
# every run artifact. Adding a name here is a commitment to say why the
# artifact is engine telemetry and not phase evidence, so the next hot
# artifact cannot slip in silently.
ENGINE_WRITTEN_RUN_ARTIFACTS = (
    # glide.nudge.touch_activity: "harness output at <epoch>", rewritten on
    # every pump tick where the harness produced output.
    "activity",
    # glide.nudge.record: the reminder audit, appended on every decision.
    "reminders.log",
)

# A run artifact is a file directly inside runs/phase-<N>/. The path may carry
# a repository prefix, because the publish check reports the private checkout's
# paths as seen from the public one.
_RUN_ARTIFACT_RE = re.compile(r"(?:^|/)runs/(phase-\d+)/([^/]+)$")


def is_engine_written_run_artifact(path: str) -> bool:
    """Whether a repo-relative path is engine telemetry the running step
    rewrites in place, rather than phase evidence.

    Args:
        path: A repo-relative path, as :func:`dirty_paths` reports it.

    Returns:
        Whether the path is an engine-written artifact of one phase.
    """
    match = _RUN_ARTIFACT_RE.search(path)
    return match is not None and match.group(2) in ENGINE_WRITTEN_RUN_ARTIFACTS


def publish_dirt(
    dirty: Iterable[str],
    root: str | Path | None = None,
    phase: int | None = None,
) -> tuple[list[str], list[str]]:
    """Split dirty paths into the ones that must refuse publication and the
    engine-written ones that were already committed and pushed.

    The excluded set is reported, never dropped: an operator reading a
    publication can see what the check chose not to look at.
    """
    if root is not None and phase is not None:
        run = phase_run_dir(root, phase).resolve()

        def engine_artifact(path: str) -> bool:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(root) / candidate
            return (
                candidate.resolve().parent == run
                and candidate.name in ENGINE_WRITTEN_RUN_ARTIFACTS
            )
    else:
        engine_artifact = is_engine_written_run_artifact
    real = [p for p in dirty if not engine_artifact(p)]
    engine = sorted(p for p in dirty if engine_artifact(p))
    return real, engine


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
    rc, _, err = git(repo, "add", "-A", "--", pathspec)
    if rc != 0:
        return degraded(
            info, f"could not stage {pathspec}: {err}; the next tick retries"
        )
    # Scoped to the pathspec: a staged path from another phase stays staged and
    # cannot ride this commit.
    rc, out, err = git(repo, "commit", "-q", "-m", message, "--", pathspec)
    if rc != 0:
        combined = err or out
        if "nothing to commit" in combined.lower():
            return dict(info, ok=True, action="clean", hint=message)
        return degraded(
            info,
            f"commit of run state failed: {combined}; the next tick retries",
        )
    for attempt in (1, 2):
        rc, out, err = git(repo, "push", remote, branch)
        if rc == 0:
            after = git(repo, "rev-parse", "HEAD")[1]
            return dict(
                info, ok=True, action="committed-and-pushed", after=after, hint=message
            )
        if attempt == 1:
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
        f"push of run state failed: {err or out}; the next tick retries",
    )


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
