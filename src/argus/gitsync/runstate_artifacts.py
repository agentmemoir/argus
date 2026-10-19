# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The engine-written run artifacts, and the publication-dirt split."""

import re
from collections.abc import Iterable
from pathlib import Path

from argus.conventions import phase_run_dir

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


__all__ = [
    "ENGINE_WRITTEN_RUN_ARTIFACTS",
    "is_engine_written_run_artifact",
    "publish_dirt",
]
