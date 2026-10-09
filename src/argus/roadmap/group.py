# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Reservation groups: the phase set one document says must run as one unit."""

import re
from pathlib import Path
from typing import Any, cast

from argus.conventions import phase_file_parts, phase_paths
from argus.roadmap.evidence import (
    index_complete,
    is_done,
    reported_path,
    resolve_roadmap,
)
from argus.select.work_source import WorkSourceError

#: The fenced heading a phase uses to declare its reservation group.
GROUP_HEADING = "### Reservation Group"

#: A ``##``/``###`` heading, which ends a group section. The same shape the
#: decision-gate reader in ``phase_policy`` uses.
GROUP_HEADING_RE = re.compile(r"^#{2,3} ")

#: The first ``together:`` line of a group section. The value may be wrapped
#: in backticks; the capture is the comma-separated phase list.
GROUP_TOGETHER_RE = re.compile(r"^\s*`?together\s*:\s*(.+?)`?\s*$", re.IGNORECASE)

#: One member of a ``together:`` list, a six-digit phase number.
GROUP_TOKEN_RE = re.compile(r"^\d{6}$")


class ReservationGroupError(WorkSourceError):
    """A phase's reservation group is declared wrongly, so it cannot run.

    A reservation group is symmetric: every member names the same set of
    phases, and the group runs as one unit. A declaration that is missing,
    asymmetric, or names a phase with no document would run the wrong work,
    so it is refused as a work-source error rather than guessed at. The
    ``document`` attribute names the phase file the mistake was found in when
    there is one to name.
    """

    def __init__(self, message: str, *, document: str | Path | None = None) -> None:
        self.document = document
        super().__init__(message)


class ReservationGroupMissing(ReservationGroupError):
    """The phase document declares no reservation group at all.

    A malformed declaration and an absent one are different problems. An
    absent one is the ordinary single-phase case the selector must keep
    serving; a malformed one must fail closed. The selector catches this
    subclass to run a phase alone, and the request program does not, so a
    request for a phase with no declaration is still refused by name.
    """


def _group_section(text: str) -> str | None:
    """Return the body of the ``### Reservation Group`` section, or ``None``.

    The section ends at the next ``##``/``###`` heading or at a ``---`` rule,
    whichever comes first, so a together line cannot bleed into the following
    section. The shape is deliberately the same as the decision-gate reader in
    ``phase_policy``: two readers of one fenced section must agree on where it
    ends, so a later change to the fence boundary must be made in both.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == GROUP_HEADING:
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if GROUP_HEADING_RE.match(line) or stripped == "---":
            break
        body.append(line)
    return "\n".join(body)


def _parse_group_members(path: str | Path, _number: int) -> list[str]:
    """The phase numbers a phase file lists in its ``together:`` declaration.

    The phase number is kept in the signature as ``_number``, unused here, so
    every reader of one phase document takes the same pair. Returns the tokens
    exactly as declared, in declaration order, so group ordering is the
    document's and not a set's. Every failure names the document, because a bad
    declaration is a property of the file an operator has to fix.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReservationGroupError(
            f"cannot read phase document {path}: {exc}", document=path
        ) from exc
    body = _group_section(text)
    if body is None:
        raise ReservationGroupMissing(
            f"{path} has no '### Reservation Group' section", document=path
        )
    value = None
    for line in body.splitlines():
        match = GROUP_TOGETHER_RE.match(line)
        if match is not None:
            value = match.group(1)
            break
    if value is None:
        raise ReservationGroupMissing(
            f"{path} has no together declaration", document=path
        )
    members: list[str] = []
    for raw in value.split(","):
        token = raw.strip().strip("`").strip()
        if not GROUP_TOKEN_RE.match(token):
            raise ReservationGroupError(
                f"{path} together declaration has an invalid phase number {token!r}",
                document=path,
            )
        members.append(token)
    if not members:
        raise ReservationGroupError(
            f"{path} together declaration lists no phases", document=path
        )
    if len(set(members)) != len(members):
        raise ReservationGroupError(
            f"{path} together declaration repeats a phase", document=path
        )
    return members


def declared_group_members(path: str | Path, number: int) -> list[str] | None:
    """The members one phase document declares, or ``None`` when it declares none.

    ``resolve_group`` answers the *resolved* group, which also reads the index to
    work out the remainder. A caller that only needs to know which group a
    document declares, such as the branch-collision scan, wants the declaration
    on its own: one file, no index, and no work spent on a remainder it will not
    use. A malformed declaration still raises, because a document whose group
    nobody can read is not a document that declares none.
    """
    try:
        return _parse_group_members(path, number)
    except ReservationGroupMissing:
        return None


def _phase_number(value: object) -> int:
    """Return ``value`` as a phase number in ``0..999999``, or raise.

    A caller may name a phase as an int or as a numeric string, so both are
    accepted. Everything else is refused rather than coerced: silently
    turning a wrong number into a real phase would resolve the wrong group
    and run the wrong slice.
    """
    if isinstance(value, bool):
        raise ReservationGroupError(f"invalid phase number: {value!r}")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if re.fullmatch(r"[0-9]+", text) is None:
            raise ReservationGroupError(f"invalid phase number: {value!r}")
        number = int(text)
    if not 0 <= number <= 999999:
        raise ReservationGroupError(f"invalid phase number: {value!r}")
    return number


def _phase_file(roadmap: str | Path, number: int) -> Path | None:
    """The phase file whose number matches, or ``None``.

    The match is the same one the selector uses: a file's first numeric
    filename token is its phase number, so a member named in a declaration is
    found the way the pipeline would find it.
    """
    for path in phase_paths(roadmap):
        parts = phase_file_parts(path)
        if parts and parts[0] == number:
            return path
    return None


def resolve_group(
    repo: str | Path, roadmap: str | Path | None, phase: int | str
) -> dict[str, Any]:
    """Resolve a phase to its reservation group and unfinished remainder.

    The group is populated from the phase documents on disk: the declaration
    is read, each member must name the same set in return, and each member
    must have a document. The remainder is the members with no completion
    evidence, in declaration order, so a caller can run the group as one unit.
    Nothing is claimed and nothing is written; this is a read of the roadmap.
    """
    number = _phase_number(phase)
    folder = resolve_roadmap(repo, roadmap)
    path = _phase_file(folder, number)
    if path is None:
        raise ReservationGroupError(f"phase {number:06d} has no document in {folder}")
    key = f"{number:06d}"
    declared = _parse_group_members(path, number)
    if key not in declared:
        raise ReservationGroupError(
            f"{path} together declaration does not list phase {key}", document=path
        )
    for member in declared:
        member_number = int(member)
        member_path = _phase_file(folder, member_number)
        if member_path is None:
            raise ReservationGroupError(
                f"group member {member} has no phase document in {folder}"
            )
        member_declared = _parse_group_members(member_path, member_number)
        if set(member_declared) != set(declared):
            raise ReservationGroupError(
                f"{member_path} declares {member_declared}, expected {declared}",
                document=member_path,
            )
    index_done = index_complete(repo, folder)
    members: list[dict[str, Any]] = []
    for member in declared:
        member_number = int(member)
        member_path = _phase_file(folder, member_number)
        members.append(
            {
                "phase": member,
                "path": reported_path(repo, cast("Path", member_path)),
                "done": is_done(
                    repo, member_number, cast("Path", member_path), index_done
                ),
            }
        )
    remainder = [entry["phase"] for entry in members if not entry["done"]]
    return {
        "state": "GROUP",
        "phase": key,
        "path": reported_path(repo, path),
        "group": list(declared),
        "remainder": remainder,
        "members": members,
    }


def resolve_group_or_none(
    repo: str | Path, roadmap: str | Path | None, phase: int | str
) -> dict[str, Any] | None:
    """The phase's group, or ``None`` when its document declares none.

    The selector must keep serving an ordinary phase that carries no
    declaration, so it asks this instead of ``resolve_group``. A declaration
    that exists but is malformed still raises, which fails the selection
    closed: only a genuinely absent declaration means "run this phase alone".
    """
    number = _phase_number(phase)
    folder = resolve_roadmap(repo, roadmap)
    path = _phase_file(folder, number)
    if path is None:
        return None
    try:
        _parse_group_members(path, number)
    except ReservationGroupMissing:
        return None
    return resolve_group(repo, roadmap, phase)
