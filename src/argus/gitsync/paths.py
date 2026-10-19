# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase and group branch names derived from phases file names."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from argus.conventions import (
    board_repo,
    phase_file_parts,
    phase_paths,
)
from argus.conventions import (
    phases_folder as _phases_folder,
)
from argus.gitsync.git import ConfigError, degraded

# A phase branch is named from the behaviour in the phase's phases file, and
# never from its number. The branch is pushed to the public repository, where
# the number reads as an internal label to every reviewer, and the behaviour is
# the part that says what the change carries. `phase-100440-backup-archive-
# format-and-directory.md` becomes `feature/backup-archive-format-and-
# directory`. A file carrying no behaviour is refused, and the number is still
# checked against the phase the caller means, so the wrong phases file cannot
# put a phase's work on another phase's branch.
PHASE_BRANCH_PREFIX = "feature/"


def _phase_file_parts(phases_name: str | None) -> tuple[str, str]:
    """Split a phases file's base name into its number and its behaviour.

    Args:
        phases_name: A phases file's base name, with or without ``.md``.

    Returns:
        ``(number, behaviour)``, for example
        ``("100440", "backup-archive-format-and-directory")``.

    Raises:
        ConfigError: The name is not a phase file, or carries no behaviour.
    """
    name = str(phases_name or "")
    parts = phase_file_parts(name if name.endswith(".md") else name + ".md")
    if parts is None:
        raise ConfigError(
            f"cannot derive a phase branch from {phases_name!r}: "
            "expected a configured phase filename with a behaviour"
        )
    number, behaviour = parts
    if not behaviour:
        raise ConfigError(f"phase file carries no behaviour: {phases_name!r}")
    return str(number), behaviour


def phase_branch(phases_name: str | None) -> str:
    """The branch name for a phase, derived from its phases file name.

    Args:
        phases_name: A phases file's base name, with or without ``.md``.

    Returns:
        The branch name, for example ``feature/backup-archive-format``. The
        phase number is deliberately absent: this branch is published to the
        public repository.

    Raises:
        ConfigError: The name is not a phase file, or carries no behaviour.
    """
    _number, behaviour = _phase_file_parts(phases_name)
    return f"{PHASE_BRANCH_PREFIX}{behaviour}"


def phase_branch_for(phase: int | str, phases_name: str | None) -> str:
    """The branch for a phase, checked against the phase number the caller
    expects. A mismatch means the wrong phases file was passed, which would
    put the phase's work on another phase's branch.

    The number is read from the file name rather than from the branch, because
    the branch no longer carries it.
    """
    number, _behaviour = _phase_file_parts(phases_name)
    if int(number) != int(phase):
        raise ConfigError(
            f"phases file {phases_name!r} belongs to phase {int(number):06d}, "
            f"not {int(phase):06d}; refusing to guess a branch name"
        )
    return phase_branch(phases_name)


def phases_folder(root: str | Path, configured: str | Path | None = None) -> Path:
    """The phases folder to scan, as a Path.

    A folder named by the caller is taken as the operator's word and is never
    silently replaced by the derived one: the branch-uniqueness check must look
    at the same phases the selector chose from, or a collision inside a
    configured folder is invisible to it. A relative value resolves against the
    repository root, matching the selector's own rule and the driver's shell.
    """
    folder = _phases_folder(root, configured)
    if folder is not None:
        return folder
    return cast("Path", board_repo(root)) / "phases"


#: The marker a group branch carries between the prefix and its lowest member's
#: behaviour. A phase branch never carries it: ``phase_branch`` puts a file's
#: behaviour straight after the prefix, so the two names differ for the same
#: behaviour and one group's branch can never be mistaken for a member's.
GROUP_BRANCH_MARKER = "group-"


def _phase_file_for(folder: Path, number: int) -> Path | None:
    """The phase file in ``folder`` whose number matches, or ``None``.

    The match is the one the selector uses: a file's first numeric filename token
    is its phase number. A file with no number and an appendix file are skipped
    by ``phase_file_parts``, so neither can be mistaken for a group member.
    """
    for path in phase_paths(folder):
        parts = phase_file_parts(path)
        if parts and int(parts[0]) == int(number):
            return path
    return None


def group_branch(
    root: str | Path,
    members: Iterable[int | str] | None,
    phases_dir: str | Path | None = None,
) -> str:
    """The one branch a reservation group's work lives on.

    A group runs as one unit, so it publishes one branch and one pull request for
    every member. The name is derived from the group's lowest member, which the
    phase documents fix and which therefore stays the same whichever member a
    given tick happens to launch from; deriving it from the member being launched
    would give one group a different branch per member, which is the opposite of
    what the group is for.

    ``members`` are the declared members, in any order, as phase numbers or
    six-digit strings.

    Raises:
        ConfigError: The group is empty, a member is not a phase number, or the
            lowest member has no document in the phases folder.
    """
    numbers: set[int] = set()
    for member in members or ():
        try:
            numbers.add(int(str(member).strip()))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"not a phase number: {member!r}") from exc
    if not numbers:
        raise ConfigError("a group branch needs at least one member")
    folder = phases_folder(root, phases_dir)
    lowest = min(numbers)
    path = _phase_file_for(folder, lowest)
    if path is None:
        raise ConfigError(
            f"group member {lowest:06d} has no phase document in {folder}; "
            "refusing to guess a branch name"
        )
    _number, behaviour = _phase_file_parts(path.name)
    return f"{PHASE_BRANCH_PREFIX}{GROUP_BRANCH_MARKER}{behaviour}"


def assert_phase_branch_unique(
    root: str | Path,
    phase: int | str,
    phases_name: str | None,
    phases_dir: str | Path | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Refuse a branch name another of this checkout's phases also earns.

    The phase number is deliberately absent from the branch name, so the
    behaviour is the only thing keeping two phases apart. A duplicate would make
    the second phase resume the first one's branch and silently inherit its
    commits, so this fails closed and leaves the rename to the operator.

    ``phases_dir`` is the folder the phases were chosen from. It defaults to
    the derived one, which is what every machine reads unless it configured
    otherwise.

    ``branch`` is the name to check, and defaults to the name derived from the
    phase's own file. A group passes its own single branch name instead, because
    the group has no one file to derive a name from. A group branch carries the
    group marker, so it can never equal a phase branch's name; a collision
    between two groups is checked separately, by ``assert_group_branch_unique``.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    branch = branch or phase_branch_for(phase, phases_name)
    info: dict[str, Any] = {
        "repo": str(root),
        "phase": f"{int(phase):06d}",
        "branch": branch,
    }
    phases = phases_folder(root, phases_dir)
    for path in phase_paths(phases):
        if phase_file_parts(path) is None:
            continue
        number, _behaviour = _phase_file_parts(path.name)
        if int(number) == int(phase) or phase_branch(path.name) != branch:
            continue
        return degraded(
            info,
            f"{branch!r} is also the branch for {path.name!r}; rename one "
            "phase file so the behaviour part differs",
        )
    return dict(info, ok=True, action="phase-branch-name-unique", branch=branch)


def assert_group_branch_unique(
    root: str | Path,
    branch: str,
    members: Iterable[int | str] | None,
    phases_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Refuse a group branch another declared group would also earn.

    Two groups whose lowest member shares a behaviour word derive the same branch
    name, and the second group would resume the first one's commits and publish
    them under one pull request. ``assert_phase_branch_unique`` cannot catch it: a
    group branch carries the group marker and a phase branch does not, so the two
    names never compare equal. The comparison here is group against group, which
    is where the collision actually lives.

    ``members`` are the declared members of the group whose branch is being cut.
    A document whose declaration cannot be read is left to the launch that will
    refuse on it, rather than stopping a branch name this group does not share.

    Returns a result dict; ok is the success flag.
    """
    from argus.phases import source as phases_source

    root = Path(root).resolve()
    wanted = sorted(f"{int(member):06d}" for member in members or ())
    info: dict[str, Any] = {"repo": str(root), "branch": branch, "group": wanted}
    folder = phases_folder(root, phases_dir)
    for path in phase_paths(folder):
        parts = phase_file_parts(path)
        if parts is None:
            continue
        try:
            other = phases_source.declared_group_members(path, int(parts[0]))
        except phases_source.ReservationGroupError:
            continue
        if not other:
            continue
        if sorted(f"{int(member):06d}" for member in other) == wanted:
            continue
        if group_branch(root, other, phases_dir) != branch:
            continue
        other_members = [f"{int(member):06d}" for member in other]
        return degraded(
            info,
            f"{branch!r} is also the branch for group {other_members!r} "
            f"in {path.name!r}; rename a member file so the lowest member's "
            "behaviour part differs",
        )
    return dict(info, ok=True, action="group-branch-name-unique")
