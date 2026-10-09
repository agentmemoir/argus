# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run-state artifacts, and the phase-scoped commit that lands leftovers."""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from argus.conventions import board_repo, phase_run_dir, runs_dir
from argus.gitsync.git import (
    ConfigError,
    _is_private_path,
    blocked,
    dirty_paths,
    git,
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
        return blocked(info, why)
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
        return blocked(info, f"could not stage {pathspec}: {err}")
    rc, out, err = git(repo, "commit", "-q", "-m", message)
    if rc != 0:
        combined = err or out
        if "nothing to commit" in combined.lower():
            return dict(info, ok=True, action="clean", hint=message)
        return blocked(info, f"commit of run state failed: {combined}")
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
                return blocked(
                    info,
                    "remote moved and could not be integrated before pushing run state",
                    detail=res.get("summary"),
                )
    return blocked(
        info,
        f"push of run state failed: {err or out}",
        hint="resolve the remote by hand, then rerun",
    )


def _phase_run_pathspec(phase: int | str, root: str | Path | None = None) -> str:
    root = Path(root or Path.cwd()).resolve()
    try:
        number = int(phase)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid phase number: {phase!r}") from None
    if number < 0 or number > 999999:
        raise ConfigError(f"phase number out of range: {number}")
    private = board_repo(root)
    if private is None:
        raise ConfigError("the reservation board repository is not configured")
    try:
        resolved = phase_run_dir(root, number).resolve()
    except ValueError as exc:
        # The run directory could not be computed at all, most often because the
        # phase file the pipeline names is gone. This is a different failure from
        # a directory that lands outside the private checkout, and reporting it
        # as one sent a reader looking at paths when the input was missing.
        raise ConfigError(
            f"cannot locate the run directory for phase {number}: {exc}"
        ) from exc
    try:
        return str(resolved.relative_to(private.resolve()))
    except ValueError as exc:
        raise ConfigError(
            "phase run directory must be inside the private checkout to publish"
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
    private = cast("Path", board_repo(root))
    pathspec = (
        _phase_run_pathspec(phase, root)
        if phase is not None
        else str(runs_dir(root).relative_to(private))
    )
    results: list[dict[str, Any]] = []
    # The nested private checkout is a separate repo by design; if a host
    # lacks the global ignore for it, do not mistake it for public dirt.
    pub_dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
    if pub_dirty:
        results.append(
            blocked(
                {"repo": str(root)},
                "public checkout has changes outside the pipeline's run state",
                dirty=pub_dirty,
                hint="commit or revert them, then rerun",
            )
        )
    if is_git_repo(private):
        paths = dirty_paths(private)
        if phase is None:
            outside = [p for p in paths if not is_run_state(p, pathspec)]
            inside = [p for p in paths if is_run_state(p, pathspec)]
        else:
            prefix = pathspec + "/"
            # Unstaged files from another phase are deliberately left alone;
            # only staged paths outside this phase are dangerous because a
            # commit would carry them accidentally.
            _, staged, _ = git(private, "diff", "--cached", "--name-only")
            outside = [
                p
                for p in staged.splitlines()
                if p != pathspec and not p.startswith(prefix)
            ]
            inside = [p for p in paths if p == pathspec or p.startswith(prefix)]
        if outside:
            results.append(
                blocked(
                    {"repo": str(private)},
                    "private checkout has changes outside the current phase"
                    if phase is not None
                    else "private checkout has changes outside runs/",
                    dirty=outside,
                    hint="commit or move them before publishing this phase",
                )
            )
        elif inside:
            results.append(
                _commit_and_push_run_state(private, message, pathspec=pathspec)
            )
        else:
            results.append({"repo": str(private), "ok": True, "action": "clean"})
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
]
