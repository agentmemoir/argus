# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Update a developer model's estimate factor from a finished phase.

A host prices each phase in ideal days and keeps one factor per developer
harness: corrected days = ideal days x factor. After a phase finishes, this
module measures what the factor would have had to be, and moves the stored
factor toward it:

* observed = phase hours / (ideal days x ``HOURS_PER_DAY``);
* the new factor is the observed one, kept within ``STEP`` of the old factor;
* a harness with no factor starts from the slowest known one, so a new model
  counts as slow until a phase shows otherwise.

A resumed run is skipped: its wall time covers only part of the phase. The
driver never calls this for a run that did not finish.

Two settings switch it on, and both must be present:

* ``estimate.factors-file``: a JSON file, ``{"factors": {"<harness>": 1.0}}``,
  inside a git checkout with an upstream;
* ``estimate.ideal-days``: a command that prints one phase's ideal days when
  the phase number is appended to it. It runs from ``repo-root``.

The factor file is committed and pushed alone. When the commit fails, the file
is put back, so the next phase's publish does not refuse a dirty tree. When the
push fails, the commit is reverted, so the next sync does not conflict on it. Nothing here
stops the line: every outcome is one log line.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path

from argus import conventions
from argus.gitsync.git import dirty_paths, git
from argus.gitsync.runstate import (
    _commit_and_push_run_state,  # pyright: ignore[reportPrivateUsage]
)

STEP = 0.2
HOURS_PER_DAY = 2.0
IDEAL_DAYS_TIMEOUT_S = 60


def bounded_factor(old: float, observed: float) -> float:
    """The observed factor, kept within ``STEP`` of the old one."""
    return min(max(observed, old * (1 - STEP)), old * (1 + STEP))


def observed_factor(seconds: float, ideal_days: float) -> float:
    """The factor this phase's wall time implies."""
    return seconds / 3600 / (ideal_days * HOURS_PER_DAY)


def start_factor(factors: Mapping[str, float], harness: str) -> float:
    """The harness's factor, or the slowest known one when it has none."""
    if harness in factors:
        return factors[harness]
    return max(factors.values(), default=1.0)


def _ideal_days(command: str, phase: int) -> float | None:
    """The phase's ideal days from the host command, or None when it gives none."""
    try:
        done = subprocess.run(
            [*shlex.split(command), str(phase)],
            cwd=conventions.setting("repo-root"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=IDEAL_DAYS_TIMEOUT_S,
        )
        days = float(done.stdout.strip()) if done.returncode == 0 else 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return days if days > 0 else None


def _developer_harness(run_dir: Path) -> str | None:
    """The harness the run recorded for its developer step."""
    try:
        record = json.loads((run_dir / "harnesses.json").read_text(encoding="utf-8"))
        harness = record["steps"]["developer"]["harness"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return harness if isinstance(harness, str) and harness else None


def _checkout_of(path: Path) -> tuple[Path, str] | None:
    """The git checkout holding ``path``, and the path inside it."""
    rc, top, _ = git(path.parent, "rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    repo = Path(top).resolve()
    try:
        return repo, path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return None


def _commit(repo: Path, rel: str, message: str) -> str:
    """Commit and push one file alone; put it back when it stays dirty.

    Returns "" on success, or what went wrong for the log line.
    """
    # Only a real commit-and-push is success. A degraded result is ok, so the
    # ok flag alone would read a failed push as done.
    result = _commit_and_push_run_state(repo, message, pathspec=rel)
    if result.get("ok") and result.get("action") == "committed-and-pushed":
        return ""
    # Committed but not pushed, most often because another machine pushed its
    # own update first. A revert, not a reset, so no history is rewritten: the
    # pair leaves the file as it was, and the next sync merges without a
    # conflict on it. The failed sync may have left a conflicted merge; abort
    # that first, then revert.
    if git(repo, "log", "-1", "--format=%s")[1] == message:
        if (repo / ".git" / "MERGE_HEAD").exists():
            git(repo, "merge", "--abort")
        git(repo, "revert", "--no-edit", "HEAD")
        return "not pushed; reverted locally"
    if rel in dirty_paths(repo):
        git(repo, "checkout", "--", rel)
        return "not committed, file put back"
    return "committed but not pushed"


def update(phase: int, run_dir: Path, seconds: float, *, resumed: bool) -> str:
    """Update the factor for one finished phase; return the line to log."""
    factors_file = conventions.setting("estimate.factors-file", None)
    command = conventions.setting("estimate.ideal-days", None)
    if not factors_file or not command:
        return ""
    head = f"estimate: phase {phase}"
    if resumed:
        return f"{head} resumed; factor not updated"
    harness = _developer_harness(run_dir)
    if harness is None:
        return f"{head}: no developer harness recorded; factor not updated"
    days = _ideal_days(command, phase)
    if days is None:
        return f"{head}: no ideal days for this phase; factor not updated"
    path = Path(factors_file)
    location = _checkout_of(path)
    if location is None:
        return f"{head}: factor file is not in a git checkout; factor not updated"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        factors = data["factors"]
        old = float(start_factor(factors, harness))
    except (OSError, ValueError, KeyError, TypeError):
        return f"{head}: factor file unreadable; factor not updated"
    observed = observed_factor(seconds, days)
    new = round(bounded_factor(old, observed), 3)
    factors[harness] = new
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    result = (
        f"{head} {harness}: {seconds / 3600:.2f} h for {days:g} ideal days, "
        f"observed {observed:.3f}, factor {old:g} -> {new:g}"
    )
    problem = _commit(*location, result)
    return f"{result}; {problem}" if problem else result
