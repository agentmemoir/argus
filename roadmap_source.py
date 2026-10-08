#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The roadmap work source: work comes from phase files in a roadmap folder.

This is the one work source that exists. It reads the phase files out of a
roadmap folder and returns the next uncompleted one. The driver never learns
which folder that is, or even that the work came from a roadmap; it asks the
configured source and gets back the result shape the driver already reads.

Roadmap files live in the configured roadmap folder. The pipeline owns
its run directory; standalone tools use the configured runs folder. A
machine may name a different roadmap folder; see ``resolve_roadmap``.

A phase is complete when any of these holds:
  * <run_dir>/run.json exists with
    "state": "completed";
  * <run_dir>/ledger.json records a finalize
    step with signal "FINALIZE_DONE";
  * <run_dir>/publication.json records a
    claim-checked published completion;
  * a <run_dir>/finalize-task-r*.log ends
    with FINALIZE_DONE;
  * the phase file carries a checked
    "- [x] ... Required approval is obtained" box (the finalize stage flips it);
  * roadmap/index.md marks it Complete (row status or
    bold heading).

The index.md source exists because phase templates drift: some files never
carry the approval box. When index.md says Complete but no other marker is
present, we warn on stderr and still treat it as done, so a completed phase is
never silently re-run.

Appendix files (phase-*-appendix-*.md) are references, never runnable phases.
Parked phases (number >= 900000) are roadmap-only and are ignored by selection,
audit, and verbose output.

A phase whose pull request is open but unmerged is finished work, not queue
work. Its receipt records ``awaiting_review``, which is not one of the
completion markers above, so the phase is not done; the server selection
returns it under ``awaiting_review`` so the driver can still run the merge
check, which is the only thing that turns a finished pull request into a
finished phase.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus.work_source import (  # noqa: E402 - path set just above
    WorkSource,
    WorkSourceError,
)
from argus.phase_policy import (  # noqa: E402 - path set just above
    is_runnable_phase,
    phase_gates_blocked,
)
from argus.phase_reservations import (  # noqa: E402 - path set just above
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationCooldown,
    ReservationStore,
    reservation_file,
    save_reservation_file,
)

from argus import conventions
from argus.conventions import phase_file_parts, phase_paths, phase_run_dir, runs_dir

INDEX_ROW_RE = re.compile(r"^\|\s*(\d{4,6})\s*\|")
INDEX_HEAD_RE = re.compile(r"^#{2,3}\s*(\d{4,6})\.\s")
APPROVAL = "Required approval is obtained"
BOARD_REL = Path("vault") / "cedar-notes"


class RoadmapUnavailable(WorkSourceError):
    """The roadmap folder this machine was told to read cannot be read.

    A work-source error, so the driver stops this tick rather than reading a
    different folder, but a distinct type because a folder that cannot be read
    and a source name nobody recognises are different mistakes.
    """


def resolve_roadmap(repo, configured=None):
    """The roadmap folder to read, or None when there is none to read.

    A configured folder is taken as the operator's word and is never second-
    guessed by falling back to something else: a machine pointed at a folder
    that does not exist must hear about that folder, not quietly read the
    canonical one and look healthy. A relative configured path is resolved
    against the repository root rather than the working directory, because cron
    starts the driver from an arbitrary one; the driver's own shell does the
    same thing for the same reason.

    A folder outside the repository is allowed. The phase file it names is then
    reported as an absolute path, because a launch inside this checkout cannot
    express a path to somewhere else; ``reported_path`` is where that happens.
    """
    base = Path(configured or conventions.setting("roadmap-dir"))
    if not base.is_absolute():
        base = Path(repo) / base
    if not base.is_dir():
        raise RoadmapUnavailable(
            f"configured roadmap folder {base} is missing or is not a directory")
    return base


def reported_path(repo, path):
    """The path the driver is told to launch, as a string.

    Repository-relative when the file is inside the repository, because that is
    what the launch has always passed and the pipeline file resolves against
    the repository root. Absolute otherwise, so a machine pointed at a folder
    outside this checkout still gets a usable path rather than an error: the
    brief asks for another checkout or another disk to work, and refusing it
    here would make the setting useless for exactly the case it exists for.
    """
    try:
        return str(Path(path).relative_to(Path(repo)))
    except ValueError:
        return str(path)


def file_approved(path):
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") and APPROVAL in stripped:
            return True
    return False


def run_dir(repo, number, phase_file=None):
    return phase_run_dir(repo, number, phase_file)


def run_completed(repo, number, phase_file=None):
    rj = run_dir(repo, number, phase_file) / "run.json"
    try:
        state = json.loads(rj.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get("state") == "completed"


#: Run states that are terminal but are not completion evidence.
#:
#: A phase that ends in one of these owes a person a decision: the remedy
#: approver budget ran out (``remedy_escalated``) or a checker rejected the
#: work outright (``rejected``). The run cannot resume on its own, and offering
#: it again made the driver recover its own claim and launch a phase that glide
#: then refused to start, which halted the line on every tick. The selector
#: therefore reports these and skips them rather than offering them. The list is
#: explicit and short on purpose: a state that is not named here is treated as
#: ordinary queue work, which fails in the direction of not stranding work.
NEEDS_DECISION_RUN_STATES = frozenset({"rejected", "remedy_escalated"})


def needs_decision(repo, number):
    """The run state when this phase ended needing a person's decision.

    Args:
        repo: The project root.
        number: The phase number.

    Returns:
        The terminal run state when it is one the selector must not offer,
        otherwise ``None``.
    """
    rj = run_dir(repo, number) / "run.json"
    try:
        state = json.loads(rj.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    value = state.get("state")
    return value if value in NEEDS_DECISION_RUN_STATES else None


def ledger_steps(repo, number, phase_file=None):
    """Parsed steps map from ledger.json, or None when missing/unusable."""
    try:
        ledger = json.loads((run_dir(repo, number, phase_file) / "ledger.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(ledger, dict):
        return None
    steps = ledger.get("steps")
    return steps if isinstance(steps, dict) else None


def ledger_present(repo, number, phase_file=None):
    """A ledger.json exists and parses far enough to carry step proofs."""
    return ledger_steps(repo, number, phase_file) is not None


def ledger_finalized(repo, number, phase_file=None):
    """ledger.json records a finalize step with a FINALIZE_DONE signal.

    The signal carries a digest: the runner writes ``FINALIZE_DONE <8 hex>``.
    Comparing the whole string to the bare word, as this did, meant no real
    phase ever matched, so a finished phase read as unfinished and
    ``is_done`` fell through to the index. ``finalize_log_done`` has always
    accepted both forms; this must too.
    """
    steps = ledger_steps(repo, number, phase_file)
    if steps is None:
        return False
    fin = steps.get("finalize")
    if not isinstance(fin, dict):
        return False
    signal = fin.get("signal")
    if not isinstance(signal, str):
        return False
    return signal == "FINALIZE_DONE" or signal.startswith("FINALIZE_DONE ")


def publication_receipt_done(repo, number, phase_file=None):
    path = run_dir(repo, number, phase_file) / "publication.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict)
            and data.get("version") == 1
            and data.get("state") == "published"
            and data.get("phase") == f"{int(number):06d}")


def finalize_log_done(repo, number, phase_file=None):
    """A finalize-task-r*.log ends with a FINALIZE_DONE signal line."""
    try:
        logs = sorted(run_dir(repo, number, phase_file).glob("finalize-task-r*.log"))
    except OSError:
        return False
    for log in logs:
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "FINALIZE_DONE" or stripped.startswith("FINALIZE_DONE "):
                return True
            break
    return False


def index_complete(repo, roadmap):
    """Phase numbers marked Complete in roadmap/index.md."""
    if roadmap is None:
        return set()
    path = roadmap / "index.md"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    done = set()
    for line in text.splitlines():
        row = INDEX_ROW_RE.match(line)
        if row and "Complete" in line:
            number = int(row.group(1))
            if is_runnable_phase(number):
                done.add(number)
            continue
        head = INDEX_HEAD_RE.match(line)
        if head and "**Complete**" in line:
            number = int(head.group(1))
            if is_runnable_phase(number):
                done.add(number)
    return done


def phase_attempted(repo, number, phase_file=None):
    """The pipeline has run this phase at least once.

    A run directory exists whatever the run went on to do: it holds the run
    record, the ledger, the receipts and the finalize logs. Its presence is
    therefore proof that the phase was reached, and not a claim that it
    finished, which is why it corroborates the approval box instead of
    completing the phase by itself.
    """
    try:
        return run_dir(repo, number, phase_file).is_dir()
    except OSError:
        return False


def is_done(repo, number, path, index_done):
    """Whether this phase's work is finished, from the machine's own records.

    The ticked "required approval is obtained" box on the phase file is
    corroborating evidence and never sufficient evidence on its own. It records
    that a gate was passed, which is a different claim from the phase being
    finished, and it is the one signal here an agent writes by hand. Read alone
    it ended a phase that had no run directory, no ledger, no receipt and no
    finalize log, so the remedy approver and finalize never ran and the line
    moved on with nothing reporting an error.

    So the box counts only once the pipeline has run the phase. That keeps the
    operator close-out for a phase the pipeline rejected or blocked, which is
    what a hand tick is for, and refuses it for a phase nothing ever ran.

    It is deliberately *not* corroborated by the ledger, the receipt or the
    finalize log. Each of those already completes a phase on its own below, so
    corroborating the box with them would leave the box unable to change any
    verdict: the tick would be read and then ignored.
    """
    if (run_completed(repo, number, path)
            or (file_approved(path) and phase_attempted(repo, number, path))
            or publication_receipt_done(repo, number, path)):
        return True
    if ledger_present(repo, number, path):
        # The ledger is runner-maintained and re-verified on resume; when it
        # parses, it outranks the raw agent-written finalize log, so a stale
        # DONE log cannot override a ledger with no finalize entry.
        return ledger_finalized(repo, number, path)
    if finalize_log_done(repo, number, path):
        return True
    if number in index_done:
        print(f"warning: phase {number} is marked Complete in index.md but has "
              f"no run.json/ledger/publication/finalize-log marker; treating it "
              f"as done",
              file=sys.stderr)
        return True
    return False


def candidates(repo, roadmap):
    out = []
    if roadmap is None:
        return out
    for path in phase_paths(roadmap):
        parts = phase_file_parts(path)
        if parts and is_runnable_phase(parts[0]):
            out.append((parts[0], path))
    return sorted(out)


def signals(repo, number, path, index_done):
    """Per-signal completion evidence for one phase (verbose/audit output)."""
    return {
        "run": run_completed(repo, number, path),
        "ledger": ledger_finalized(repo, number, path),
        "finalize_log": finalize_log_done(repo, number, path),
        "publication": publication_receipt_done(repo, number, path),
        "approval": file_approved(path),
        "index": number in index_done,
        # Not completion evidence, but the other half of what a reader needs:
        # a phase that ended needing a decision is neither done nor offerable.
        "needs_decision": needs_decision(repo, number) is not None,
    }


def audit(repo, roadmap, apply_index=False):
    """Report index.md drift. Returns issue count.

    With ``apply_index`` the second drift class is also repaired: a phase that
    carries a completion marker but is not indexed Complete gets its row
    annotated in place. ``index_complete`` looks for the word ``Complete`` in
    a ``| <number> |`` row, so that annotation is what closes the drift, and it
    goes on the row's last cell rather than in a new column, because these
    tables vary in width and adding a column would break their headers.

    The first class is never repaired automatically: a phase indexed Complete
    with no marker is a claim the evidence does not support, and dropping the
    index entry is an operator decision, not a mechanical fix.
    """
    index_done = index_complete(repo, roadmap)
    issues = 0
    drifted = []
    for number, path in candidates(repo, roadmap):
        s = signals(repo, number, path, index_done)
        markers = (s["run"] or s["ledger"] or s["finalize_log"]
                   or s["publication"] or s["approval"])
        indexed = number in index_done
        if indexed and not markers:
            print(f"drift: phase {number} indexed Complete but has no marker "
                  f"({path.name})")
            issues += 1
        elif markers and not indexed:
            print(f"drift: phase {number} has completion markers but index is "
                  f"not Complete ({path.name})")
            issues += 1
            drifted.append((number, path))
        if s["needs_decision"] and not indexed:
            # Reported, not counted: this is not index drift, it is a phase the
            # selector is passing over until a person decides. Naming it here is
            # what stops the skip being silent.
            print(f"decision: phase {number} ended needing a decision and is "
                  f"not selected ({path.name})")
    if apply_index and drifted:
        applied, skipped = _annotate_index_complete(repo, roadmap, drifted)
        print(f"index: marked {applied} entr(ies) Complete"
              + (f", {skipped} left for a human" if skipped else ""))
    return issues


def _annotate_index_complete(repo, roadmap, drifted):
    """Mark each named phase's index.md entry Complete, in place.

    Two shapes exist and they need different edits:

    * A heading (``## 100155. Title ...``) gets ``· **Complete** ·`` inserted
      before its link, matching the convention the already-complete phases use.
    * A table row is handled by its table's header. When the header names a
      Status column, that cell is *replaced*, because it holds a real value
      such as ``Plan ready`` and appending would produce a contradiction like
      ``Plan ready | — Complete``. When the table has no Status column, the
      marker goes on the last cell.

    A row or heading that already says Complete is left alone, so this is safe
    to re-run. Anything not matching one of those shapes is reported, not
    guessed at.
    """
    if roadmap is None:
        return 0, 0
    path = roadmap / "index.md"
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"index: cannot read index.md ({exc})")
        return 0, 0
    wanted = {f"{number:06d}" for number, _ in drifted}
    lines = text.splitlines(keepends=True)
    # Column 0 is the phase number in every table here. The header labels it
    # "Slice" or "Phase" rather than a number, so the status column is tracked
    # positionally from the header row, and reset when a new table starts.
    status_col = None
    applied = 0
    skipped = 0
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = line.rstrip("\n").split("|")
            if _is_header_row(cells):
                status_col = _status_column_index(cells)
                out.append(line)
                continue
        if not stripped.startswith("|"):
            # Outside any table: headings are the other entry shape.
            head = INDEX_HEAD_RE.match(line)
            if head and head.group(1) in wanted and "Complete" not in line:
                new = _mark_heading_complete(line)
                if new is None:
                    skipped += 1
                else:
                    applied += 1
                out.append(new or line)
                continue
        row = INDEX_ROW_RE.match(line)
        if not row or row.group(1) not in wanted or "Complete" in line:
            out.append(line)
            continue
        new = _mark_row_complete(line, status_col)
        if new is None:
            skipped += 1
            out.append(line)
        else:
            applied += 1
            out.append(new)
    if applied:
        path.write_text("".join(out))
    return applied, skipped


def _is_header_row(cells):
    """True for a table header: a non-numeric first cell naming the column.

    ``cells`` is the raw ``split("|")``, so index 0 is the empty string before
    the leading bar and the labels start at index 1.
    """
    labels = [c.strip() for c in cells[1:-1]]
    if not labels or labels[0].isdigit():
        return False
    return labels[0].lower() in {"slice", "phase", "number", "id"}


def _status_column_index(cells):
    """Index into a raw ``split("|")`` row for the Status cell, or None."""
    for idx, cell in enumerate(cells[1:-1], start=1):
        if cell.strip().lower().startswith("status"):
            return idx
    return None


def _mark_heading_complete(line):
    """Insert the marker before the link, not inside it."""
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    start = body.find("[")
    if start == -1:
        return None
    before = body[:start].rstrip()
    if before.endswith("·"):
        return f"{before} **Complete** · {body[start:]}{newline}"
    return f"{before} · **Complete** · {body[start:]}{newline}"


def _mark_row_complete(line, status_col):
    """Replace the Status cell when the table has one, else mark the last cell.

    The rebuilt row keeps the line's original indentation and column widths by
    editing only the target cell in place, so the file stays diffable and
    renders the same as before apart from the status text.
    """
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    # Split on the cell separator, keeping the empties produced by the leading
    # and trailing "|", so cells[0] is "" and the last is "".
    cells = body.split("|")
    if len(cells) < 3:
        return None
    if status_col is not None and 0 < status_col < len(cells) - 1:
        cells[status_col] = " Complete — PASS WITH DOCUMENTED LIMITATIONS "
    else:
        last = len(cells) - 2
        cells[last] = f"{cells[last].rstrip()} — Complete "
    return "|".join(cells) + newline


def select_next(repo, roadmap):
    index_done = index_complete(repo, roadmap)
    for number, path in candidates(repo, roadmap):
        if not is_done(repo, number, path, index_done):
            return number, path
    return None


def gate_block(number, path):
    """Return a BLOCKED_ON_GATE result when the plan owes the owner a decision.

    A phase with an ``open`` decision gate is not runnable, and the whole point
    of recording the gate in the plan is that this check finds it before a
    reservation is spent. Phase 100817 ran a full round and stopped on its own
    mandatory stop condition because the gate was real but the selector was not
    reading it. Fails closed: a plan whose gate section cannot be parsed is
    reported as blocked rather than launched, because an unreadable gate and an
    absent gate are the same risk.
    """
    try:
        blocked = phase_gates_blocked(path)
    except OSError:
        return {
            "state": "BLOCKED_ON_GATE",
            "phase": f"{number:06d}",
            "path": str(path),
            "error": "decision gate section could not be read",
        }
    if blocked:
        return {
            "state": "BLOCKED_ON_GATE",
            "phase": f"{number:06d}",
            "path": str(path),
            "error": "open decision gate; the owner must record it first",
        }
    return None


def _gitsync(repo):
    """Return the publisher shipped with this Argus package."""
    try:
        from argus.gitsync import awaiting_review
    except ImportError as exc:
        raise ImportError("the Argus package is missing its bundled publisher") from exc
    return awaiting_review


def _awaiting_review(repo, number):
    """A phase whose pull request is waiting for a human, or None.

    A phase that has been pushed but not merged is finished work, not queue
    work. The selector must not offer it for another run, and it must not skip
    past it either: the line waits until a person merges.
    """
    return _gitsync(repo)(repo, number)


def _reservation_result(number, path, record, state="RESERVED"):
    return {
        "state": state,
        "phase": f"{number:06d}",
        "path": str(path),
        "machine_id": record.get("machine_id"),
        "status": record.get("status"),
        "reservation_id": record.get("reservation_id"),
        "generation": record.get("generation"),
        "run_id": record.get("run_id"),
    }


#: The fenced heading a phase uses to declare its reservation group.
GROUP_HEADING = "### Reservation Group"

#: A ``##``/``###`` heading, which ends a group section. The same shape the
#: decision-gate reader in ``phase_policy`` uses.
GROUP_HEADING_RE = re.compile(r"^#{2,3} ")

#: The first ``together:`` line of a group section. The value may be wrapped
#: in backticks; the capture is the comma-separated phase list.
GROUP_TOGETHER_RE = re.compile(
    r"^\s*`?together\s*:\s*(.+?)`?\s*$", re.IGNORECASE)

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

    def __init__(self, message, *, document=None):
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


def _group_section(text):
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
    body = []
    for line in lines[start:]:
        stripped = line.strip()
        if GROUP_HEADING_RE.match(line) or stripped == "---":
            break
        body.append(line)
    return "\n".join(body)


def _parse_group_members(path, number):
    """The phase numbers a phase file lists in its ``together:`` declaration.

    ``number`` is the phase being read, kept in the signature so every reader
    of one phase document takes the same pair. Returns the tokens exactly as
    declared, in declaration order, so group ordering is the document's and
    not a set's. Every failure names the document, because a bad declaration
    is a property of the file an operator has to fix.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReservationGroupError(
            f"cannot read phase document {path}: {exc}",
            document=path) from exc
    body = _group_section(text)
    if body is None:
        raise ReservationGroupMissing(
            f"{path} has no '### Reservation Group' section", document=path)
    value = None
    for line in body.splitlines():
        match = GROUP_TOGETHER_RE.match(line)
        if match is not None:
            value = match.group(1)
            break
    if value is None:
        raise ReservationGroupMissing(
            f"{path} has no together declaration", document=path)
    members = []
    for raw in value.split(","):
        token = raw.strip().strip("`").strip()
        if not GROUP_TOKEN_RE.match(token):
            raise ReservationGroupError(
                f"{path} together declaration has an invalid phase number "
                f"{token!r}", document=path)
        members.append(token)
    if not members:
        raise ReservationGroupError(
            f"{path} together declaration lists no phases", document=path)
    if len(set(members)) != len(members):
        raise ReservationGroupError(
            f"{path} together declaration repeats a phase", document=path)
    return members


def _phase_number(value):
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


def _phase_file(roadmap, number):
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


def resolve_group(repo, roadmap, phase):
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
        raise ReservationGroupError(
            f"phase {number:06d} has no document in {folder}")
    key = f"{number:06d}"
    declared = _parse_group_members(path, number)
    if key not in declared:
        raise ReservationGroupError(
            f"{path} together declaration does not list phase {key}",
            document=path)
    for member in declared:
        member_number = int(member)
        member_path = _phase_file(folder, member_number)
        if member_path is None:
            raise ReservationGroupError(
                f"group member {member} has no phase document in {folder}")
        member_declared = _parse_group_members(member_path, member_number)
        if set(member_declared) != set(declared):
            raise ReservationGroupError(
                f"{member_path} declares {member_declared}, expected "
                f"{declared}", document=member_path)
    index_done = index_complete(repo, folder)
    members = []
    for member in declared:
        member_number = int(member)
        member_path = _phase_file(folder, member_number)
        members.append({
            "phase": member,
            "path": reported_path(repo, member_path),
            "done": is_done(repo, member_number, member_path, index_done),
        })
    remainder = [entry["phase"] for entry in members if not entry["done"]]
    return {
        "state": "GROUP",
        "phase": key,
        "path": reported_path(repo, path),
        "group": list(declared),
        "remainder": remainder,
        "members": members,
    }


def resolve_group_or_none(repo, roadmap, phase):
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


def _select_group(repo, number, path, group, machine_id, store, *, reserve,
                  reviews):
    """Claim a declared group's unfinished members, or report why not.

    The remainder drops completed members. A member whose pull request is open
    is finished work too, so it is dropped from the claim and recorded under
    ``reviews``, which is what lets the driver run the merge check on it. If
    nothing is left to claim the group has no queue work this tick and ``None``
    is returned, so the caller moves to the next phase.
    """
    key = f"{number:06d}"
    remainder = list(group["remainder"])
    claimable = []
    for member in remainder:
        review = _awaiting_review(repo, int(member))
        if review:
            reviews.append({
                "phase": member,
                "branch": review.get("branch"),
                "pull_request": review.get("pull_request"),
                "head": review.get("head"),
            })
        else:
            claimable.append(member)
    if not claimable:
        return None
    if not reserve:
        result = _reservation_result(number, path, {}, "AVAILABLE")
        result["path"] = reported_path(repo, path)
        result["group"] = list(group["group"])
        result["remainder"] = remainder
        return result
    try:
        records = store.reserve_group(claimable, machine_id)
    except ReservationCooldown as exc:
        return {
            "state": "COOLDOWN",
            "phase": exc.phase,
            "path": reported_path(repo, path),
            "remaining_seconds": exc.remaining_seconds,
            "error": str(exc),
        }
    except ReservationConflict as exc:
        # The push race lost. Read the winner rather than force the claim. The
        # declared group travels with the refusal, because the tick that
        # continues this group — or takes it over from a dead owner — has to
        # know which array and which one branch it is running.
        latest, _latest_commit = store.read()
        winner = latest.get("phases", {}).get(exc.phase, exc.record) or {}
        return {
            "state": "WAIT_FOR_CLAIM",
            "phase": exc.phase,
            "path": reported_path(repo, path),
            "owner": winner.get("machine_id"),
            "status": winner.get("status"),
            "reservation_id": winner.get("reservation_id"),
            "generation": winner.get("generation"),
            "group": list(group["group"]),
        }
    for record in records:
        token_path = reservation_file(repo, record["phase"])
        try:
            save_reservation_file(token_path, record)
        except OSError as exc:
            raise CoordinationError(
                f"could not persist phase reservation token: {exc}") from exc
    lead = next((r for r in records if r["phase"] == key), records[0])
    result = _reservation_result(number, path, lead)
    result["path"] = reported_path(repo, path)
    result["group"] = list(group["group"])
    result["members"] = records
    return result


def select_server_phase(repo, machine_id, *, reserve=True, store=None, roadmap=None):
    """Select the lowest incomplete phase and claim it on the shared board.

    A future claim is intentionally ignored until it becomes the lowest
    unfinished phase.  At that point the server waits; it never jumps over a
    blocked, paused, rejected, or actively running phase.

    One exception: a phase that already carries completion evidence on disk is
    finished work, not queue work.  An operator close-out (a ticked approval
    box, a published receipt, a completed run) can leave a live claim behind,
    because the board has no state for "an operator closed this".  Gating the
    line on that stale record would strand every later phase behind a phase
    nobody is working on.  Coordination claiming ``completed`` with no evidence
    at all is still an inconsistency and still fails closed.

    A second exception, and the reason this no longer stops on an open pull
    request: a phase that has been pushed and is waiting for a human merge is
    also finished work.  It is skipped rather than offered again, and the line
    continues, because the alternative was a line that stopped for as long as
    any pull request stayed open and recovered from nothing — an abandoned pull
    request parked the whole pipeline with no way back.  The skipped phases are
    reported under ``awaiting_review`` so the driver can still run the merge
    check, which is the only thing that turns a finished pull request into a
    finished phase.
    """
    machine_id = machine_id or conventions.setting("machine.id")
    store = store or ReservationStore(repo)
    try:
        state, _commit = store.read()
    except CoordinationUnavailable:
        # The board's branch does not exist yet on a machine nobody has
        # migrated. Reading it may not create it -- that is what --read-only
        # means, and a read that published to the remote would make inspecting
        # the board a write. Reserving is a write, so on that path the branch is
        # created here, once, and the read is retried. A transport failure raises
        # the same error again and is reported as itself.
        if not reserve:
            raise
        store.migrate_board()
        state, _commit = store.read()
    index_done = index_complete(repo, roadmap)
    reviews = []
    decisions = []

    def finish(result):
        # Attached to every outcome, including the ones that do not launch, so
        # the merge check runs on a tick that selected nothing too. Absent when
        # nothing was skipped, which is the ordinary case.
        if reviews:
            result["awaiting_review"] = list(reviews)
        # Phases the selector passed over because they ended owing a decision.
        # Reported on every outcome so the driver can say so in --dry-run and a
        # person can see what the line moved past.
        if decisions:
            result["needs_decision"] = list(decisions)
        return result

    def held_answer(error=None):
        """The answer for a phase the board already holds.

        A held phase may be one member of a declared reservation group. The tick
        that continues that group — or takes it over from a dead owner — reaches
        this answer rather than the group path below, because this is where a
        phase the board already holds is answered. It needs the whole array and
        the group's one branch, so the declaration travels with the refusal as it
        does on the group path. A malformed declaration fails the selection closed
        here too, for the same reason it does below.
        """
        result = {
            "state": "WAIT_FOR_CLAIM",
            "phase": key,
            "path": reported_path(repo, path),
            "owner": record.get("machine_id"),
            "status": record.get("status"),
            "reservation_id": record.get("reservation_id"),
            "generation": record.get("generation"),
        }
        if error is not None:
            result["error"] = error
        try:
            declared = resolve_group_or_none(repo, roadmap, number)
        except WorkSourceError as exc:
            return {
                "state": "GROUP_UNAVAILABLE",
                "phase": key,
                "path": reported_path(repo, path),
                "error": str(exc),
            }
        if declared is not None:
            result["group"] = list(declared["group"])
        return result

    for number, path in candidates(repo, roadmap):
        key = f"{number:06d}"
        record = state.get("phases", {}).get(key)
        done = is_done(repo, number, path, index_done)
        # A phase whose pull request is open is finished work, not queue work,
        # so it is never offered for another run. It used to stop the line here,
        # which meant an abandoned pull request stopped every later phase for
        # ever and nothing detected that it would never be merged. The line now
        # moves on, and the next phase is cut from this one's branch so it still
        # inherits the code.
        #
        # The skipped phases ride along in the result rather than being dropped:
        # the driver uses them to run the merge check, which is the only thing
        # that turns a finished pull request into a finished phase. Skipping
        # silently would leave the receipt reading awaiting_review for ever,
        # because nothing else ever revisits it.
        #
        # Checked before, and independently of, `done`. Every phase that
        # publishes has a finished run, so a report gated on `not done` never
        # fires for a real phase — the merge check was told about nothing at all,
        # every receipt stayed awaiting_review for ever, and the next phase was
        # cut from a branch whose content was already on the integration branch.
        # That is the stall, and it was caused by this report being unreachable
        # in the only state that matters.
        review = _awaiting_review(repo, number)
        if review:
            reviews.append({
                "phase": key,
                "branch": review.get("branch"),
                "pull_request": review.get("pull_request"),
                "head": review.get("head"),
            })
            continue
        if not done:
            # An open decision gate is checked before anything else that can
            # claim this phase. Reserving a phase that owes the owner a
            # decision spends a reservation, a branch, and a full agent round
            # only to have the developer stop on its own stop condition. The
            # line waits here instead, and it does not skip past: the phase
            # keeps its place, so the order of phases that do run is unchanged.
            blocked = gate_block(number, path)
            if blocked:
                blocked["path"] = reported_path(repo, path)
                return finish(blocked)
        # A run that ended rejected or remedy_escalated is not completion
        # evidence and cannot resume without an explicit operator action. It is
        # reported and passed over, never offered: offering it made the driver
        # recover its own claim and launch a phase glide refuses to start
        # without --fresh, so the tick exited 2 and the line halted on every
        # tick. The phase keeps its place in the queue, but it is not this
        # tick's work. It is not consulted for a phase that is `done`, and it
        # sits after the board's completion-inconsistency check below, so a
        # board claiming "completed" with no evidence is still reported rather
        # than quietly skipped.
        ended = None if done else needs_decision(repo, number)
        if record is not None:
            status = record.get("status")
            # Finished on disk, and the board is not asserting a completion we
            # can check: skip it. The claim is stale either way.
            if done and status != "completed":
                continue
            if status in {"completed", "accepted"}:
                if done:
                    continue
                return finish({
                    "state": "COORDINATION_INCONSISTENT",
                    "phase": key,
                    "path": reported_path(repo, path),
                    "error": (f"coordination says {status} but no completion "
                              "evidence exists"),
                })
            if ended:
                decisions.append({
                    "phase": key,
                    "path": reported_path(repo, path),
                    "state": ended,
                })
                continue
            if status in {"claimed", "running", "paused", "blocked"}:
                return finish(held_answer())
            return finish(held_answer("explicit operator takeover is required"))
        if done:
            continue
        if ended:
            decisions.append({
                "phase": key,
                "path": reported_path(repo, path),
                "state": ended,
            })
            continue
        # A phase may declare a reservation group. The group is claimed as one
        # unit under one reservation id, and the answer carries the array. A
        # phase whose document declares no group is selected alone, as before,
        # so the single-phase contract is unchanged for it.
        try:
            group = resolve_group_or_none(repo, roadmap, number)
        except WorkSourceError as exc:
            result = {
                "state": "GROUP_UNAVAILABLE",
                "phase": key,
                "path": reported_path(repo, path),
                "error": str(exc),
            }
            document = getattr(exc, "document", None)
            if document is not None:
                result["document"] = str(document)
            return finish(result)
        if group is not None:
            selected = _select_group(
                repo, number, path, group, machine_id, store,
                reserve=reserve, reviews=reviews)
            if selected is None:
                continue
            return finish(selected)
        if not reserve:
            result = _reservation_result(number, path, {}, "AVAILABLE")
            result["path"] = reported_path(repo, path)
            return finish(result)
        try:
            record = store.reserve(number, machine_id,
                                   run_id=f"phase-{key}")
        except ReservationConflict as exc:
            # The push race lost.  Read the winner rather than attempting an
            # overwrite or silently selecting a later phase.
            latest, _latest_commit = store.read()
            winner = latest.get("phases", {}).get(key, {})
            return finish({
                "state": "WAIT_FOR_CLAIM",
                "phase": key,
                "path": reported_path(repo, path),
                "owner": winner.get("machine_id", exc.record.get("machine_id")),
                "status": winner.get("status", exc.record.get("status")),
                "reservation_id": winner.get("reservation_id"),
                "generation": winner.get("generation"),
            })
        result = _reservation_result(number, path, record)
        result["path"] = reported_path(repo, path)
        # The token is written beside the store's own checkout, never beside
        # whichever board checkout is configured. The store was handed a board
        # repository explicitly, and resolving the token's path a second time
        # from the environment is how a caller that built its own checkout
        # ended up writing a real machine's run state.
        token_path = reservation_file(repo, number)
        try:
            save_reservation_file(token_path, record)
        except OSError as exc:
            raise CoordinationError(
                f"could not persist phase reservation token: {exc}") from exc
        return finish(result)
    return finish({"state": "NO_PHASE"})


class RoadmapSource(WorkSource):
    """Work from the phase files in a roadmap folder.

    ``select`` is the whole contract the driver depends on; the rest is the
    roadmap's own reporting, which the selector's diagnostic flags drive.

    An instance is built once per invocation and holds the folder it resolved,
    so no caller re-derives the layout and two parts of one tick cannot end up
    reading different folders.
    """

    name = "roadmap"

    def __init__(self, repo, roadmap=None):
        self.repo = Path(repo)
        self.roadmap = resolve_roadmap(self.repo, roadmap)

    def select(self, machine_id=None, *, reserve=True, store=None):
        """Return the next claimable phase as a result dict, claiming it."""
        return select_server_phase(self.repo, machine_id, reserve=reserve,
                                   store=store, roadmap=self.roadmap)

    def select_next(self):
        """The lowest incomplete phase, or None. Never claims anything."""
        return select_next(self.repo, self.roadmap)

    def candidates(self):
        """Every runnable phase file, lowest number first."""
        return candidates(self.repo, self.roadmap)

    def index_complete(self):
        return index_complete(self.repo, self.roadmap)

    def signals(self, item):
        return signals(self.repo, item[0], item[1], self.index_complete())

    def audit(self, apply_index=False):
        return audit(self.repo, self.roadmap, apply_index=apply_index)


def self_test():
    conventions.configure({
        "repo-root": Path("."),
        "roadmap-dir": Path("roadmap"),
        "driver.runs": Path("runs"),
        "driver.include": ["phase-*.md"],
        "driver.exclude": [],
        "pipeline": None,
        "machine.id": "self-test",
        "board.repo": BOARD_REL,
        "board.file": Path("claims.json"),
        "board.branch": "fixture-claims",
        "board.remote": "origin",
        "commands.git": "git",
        "commands.gh": "gh",
    })
    with tempfile.TemporaryDirectory() as tmp:
        # A reservation group is declared in a fenced ``### Reservation Group``
        # section, read with the same section shape as a decision gate, and is
        # symmetric: every member names the same set. Resolution is read-only;
        # it names the group and the members still to run but claims nothing.
        # These assertions cover the group a phase resolves to and the
        # documents that are refused.
        repo = Path(tmp).resolve()
        roadmap = repo / "roadmap"
        roadmap.mkdir()
        old = conventions.override({
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": repo / "runs",
        })
        try:
            def write_phase(name, together=None):
                text = "## 1. Objective\n"
                if together is not None:
                    text += "### Reservation Group\n\n"
                    text += f"`together: {together}`\n"
                (roadmap / name).write_text(text)

            # A symmetric three-member group with one member finished: the
            # remainder is the other two, in declaration order.
            for name in ("phase-100410-a.md", "phase-100420-b.md",
                         "phase-100430-c.md"):
                write_phase(name, "100410, 100420, 100430")
            (repo / "runs" / "phase-100410").mkdir(parents=True)
            (repo / "runs" / "phase-100410" / "run.json").write_text(
                json.dumps({"state": "completed"}))
            got = resolve_group(repo, roadmap, 100420)
            assert got["state"] == "GROUP", got
            assert got["group"] == ["100410", "100420", "100430"], got
            assert got["remainder"] == ["100420", "100430"], got
            from_other = resolve_group(repo, roadmap, 100410)
            assert from_other["group"] == got["group"], from_other
            assert from_other["remainder"] == ["100420", "100430"], from_other

            # Every member finished means nothing is left to run.
            for number in (100420, 100430):
                rd = repo / "runs" / f"phase-{number}"
                rd.mkdir(parents=True, exist_ok=True)
                (rd / "run.json").write_text(
                    json.dumps({"state": "completed"}))
            assert resolve_group(repo, roadmap, 100430)["remainder"] == [], \
                resolve_group(repo, roadmap, 100430)

            # A phase with no group section is refused, and the message says
            # which section is missing.
            write_phase("phase-100440-d.md")
            try:
                resolve_group(repo, roadmap, 100440)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "no '### Reservation Group' section" in str(exc), exc
            assert refused, "a phase with no together declaration is refused by name"

            # Asymmetric membership: 100460 names a different set from the
            # group 100450 declares, so the mismatching document is named.
            write_phase("phase-100450-e.md", "100450, 100460")
            write_phase("phase-100460-f.md", "100450, 100470")
            write_phase("phase-100470-g.md", "100470, 100450")
            try:
                resolve_group(repo, roadmap, 100450)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100460" in str(exc), exc
            assert refused, "an asymmetric group must be refused"

            # A member named in a declaration that has no document.
            write_phase("phase-100480-h.md", "100480, 100490")
            try:
                resolve_group(repo, roadmap, 100480)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100490" in str(exc), exc
            assert refused, "a group member with no document must be refused"

            # A phase whose own declaration does not list it cannot resolve.
            write_phase("phase-100500-i.md", "100510")
            write_phase("phase-100510-l.md", "100510")
            try:
                resolve_group(repo, roadmap, 100500)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100500" in str(exc), exc
            assert refused, "a phase not listed in its own group must be refused"

            # A token that is not a six-digit phase number.
            write_phase("phase-100520-j.md", "12345")
            try:
                resolve_group(repo, roadmap, 100520)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "12345" in str(exc), exc
            assert refused, "an invalid phase token must be refused"

            # A group of one is legal; it is unfinished until done.
            write_phase("phase-100530-k.md", "100530")
            solo = resolve_group(repo, roadmap, 100530)
            assert solo["group"] == ["100530"], solo
            assert solo["remainder"] == ["100530"], solo
        finally:
            conventions.configure(old)
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "runs" / "phase-100030").mkdir(parents=True)
        (repo / "runs" / "phase-100040").mkdir(parents=True)
        (repo / "roadmap" / "phase-100010-alpha.md").write_text(
            "- [x] Required approval is obtained.\n")
        (repo / "roadmap" / "phase-100020-appendix-notes.md").write_text("x\n")
        (repo / "roadmap" / "phase-100020-beta.md").write_text(
            "- [ ] Required approval is obtained.\n")
        (repo / "roadmap" / "phase-100030-gamma.md").write_text("x\n")
        (repo / "runs" / "phase-100030" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        # 100040: only index.md marks it Complete (template drift case).
        (repo / "roadmap" / "phase-100040-delta.md").write_text("x\n")
        (repo / "runs" / "phase-100040" / "run.json").write_text(
            json.dumps({"state": "rejected"}))
        (repo / "roadmap" / "index.md").write_text(
            "| 100040 | `1×` | [x](phase-100040-delta.md) | Complete — PASS |\n"
            "| 100020 | `1×` | [x](phase-100020-beta.md) | Plan ready |\n"
            "## 100010. alpha · `1×` · **Complete** · [x](phase-100010-alpha.md)\n")
        got = select_next(repo, repo / "roadmap")
        assert got is not None and got[0] == 100020, got
        assert got[1].name == "phase-100020-beta.md", got
        # Closed out by hand, the way an operator retires a phase the pipeline
        # rejected. The run record is what makes the tick count; without it this
        # phase is one nothing ever ran and the tick is ignored.
        (repo / "runs" / "phase-100020").mkdir(parents=True)
        (repo / "runs" / "phase-100020" / "run.json").write_text(
            json.dumps({"state": "rejected"}))
        (repo / "roadmap" / "phase-100020-beta.md").write_text(
            "- [x] Required approval is obtained.\n")
        assert select_next(repo, repo / "roadmap") is None, \
            select_next(repo, repo / "roadmap")
    with tempfile.TemporaryDirectory() as tmp:
        # Parked phases stay visible in the roadmap but are never selected.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "phase-100010-alpha.md").write_text(
            "- [x] Required approval is obtained.\n")
        (repo / "roadmap" / "phase-900999-parked.md").write_text("x\n")
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        parked_rd = repo / "runs" / "phase-100010"
        parked_rd.mkdir(parents=True)
        (parked_rd / "run.json").write_text(json.dumps({"state": "rejected"}))
        assert select_next(repo, repo / "roadmap") is None, \
            select_next(repo, repo / "roadmap")
        assert [n for n, _ in candidates(repo, repo / "roadmap")] == [100010], \
            candidates(repo, repo / "roadmap")
    with tempfile.TemporaryDirectory() as tmp:
        # A ticked approval box on a phase nothing ever ran does not finish it.
        # The box records that a gate was passed; only the pipeline's own records
        # say the work is done. Read on its own it ended a phase with no run
        # directory, no ledger, no receipt and no finalize log, so the remedy
        # approver and finalize were skipped and the line moved on with nothing
        # reporting an error.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        phase = repo / "roadmap" / "phase-100010-alpha.md"
        phase.write_text("- [x] Required approval is obtained.\n")
        assert not phase_attempted(repo, 100010), \
            "a phase with no run directory has not been attempted"
        assert not is_done(repo, 100010, phase, set()), \
            "a ticked approval box must not finish a phase nothing ever ran"
        assert select_next(repo, repo / "roadmap") is not None, \
            "a phase with only a ticked approval box is still offered"
        # The box is still reported, so the audit output shows why a human
        # believes the phase is finished.
        assert signals(repo, 100010, phase, set())["approval"] is True, \
            signals(repo, 100010, phase, set())
        # Once the pipeline has run the phase, the same box closes it out. A
        # rejected run is not completion on its own, so this is the tick doing
        # the work, which is the operator close-out this rule exists to keep.
        rd = repo / "runs" / "phase-100010"
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({"state": "rejected"}))
        assert phase_attempted(repo, 100010), \
            "a run directory is proof the phase was reached"
        assert not run_completed(repo, 100010), \
            "a rejected run is not completion"
        assert is_done(repo, 100010, phase, set()), \
            "a ticked box closes out a phase the pipeline already ran"
    with tempfile.TemporaryDirectory() as tmp:
        # Each machine signal on its own phase: ledger-only and log-only each
        # count; a ledger without finalize outranks a stale DONE log; BLOCKED
        # logs and bare run dirs never count.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        unchecked = "- [ ] Required approval is obtained.\n"
        (repo / "roadmap" / "phase-100010-alpha.md").write_text(unchecked)
        rd = repo / "runs" / "phase-100010"
        rd.mkdir(parents=True)
        (rd / "ledger.json").write_text(
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONE"}}}))
        # The runner writes the digest form, not the bare word. This case is
        # the one that regressed: with an exact `== "FINALIZE_DONE"` compare,
        # the real shape never matched, so every finished phase read as
        # unfinished. Assert it directly so it cannot come back silently, on
        # numbers this block does not otherwise use, so `select_next` below is
        # unaffected.
        rd_digest = repo / "runs" / "phase-100070"
        rd_digest.mkdir(parents=True)
        (rd_digest / "ledger.json").write_text(
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONE 3d0dbe62"}}}))
        assert ledger_finalized(repo, 100070), "a digested FINALIZE_DONE must count"
        # A near miss must not: same prefix, different word.
        rd_near = repo / "runs" / "phase-100080"
        rd_near.mkdir(parents=True)
        (rd_near / "ledger.json").write_text(
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONEA 3d0dbe62"}}}))
        assert not ledger_finalized(repo, 100080), "FINALIZE_DONEA is not FINALIZE_DONE"
        (repo / "roadmap" / "phase-100020-beta.md").write_text(unchecked)
        rd2 = repo / "runs" / "phase-100020"
        rd2.mkdir(parents=True)
        (rd2 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out complete\nFINALIZE_DONE r1nonce\n")
        (repo / "roadmap" / "phase-100030-gamma.md").write_text(unchecked)
        rd3 = repo / "runs" / "phase-100030"
        rd3.mkdir(parents=True)
        (rd3 / "ledger.json").write_text(
            json.dumps({"steps": {"developer": {"signal": "DEVELOPER_DONE"}}}))
        (rd3 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out complete\nFINALIZE_DONE\n")
        (repo / "roadmap" / "phase-100040-delta.md").write_text(unchecked)
        rd4 = repo / "runs" / "phase-100040"
        rd4.mkdir(parents=True)
        (rd4 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out blocked\nFINALIZE_BLOCKED: trouble\n")
        (repo / "roadmap" / "phase-100050-epsilon.md").write_text(unchecked)
        (repo / "runs" / "phase-100050").mkdir(parents=True)
        (repo / "roadmap" / "phase-100060-zeta.md").write_text(unchecked)
        got = select_next(repo, repo / "roadmap")
        assert got is not None and got[0] == 100030, got
        assert got[1].name == "phase-100030-gamma.md", got
    with tempfile.TemporaryDirectory() as tmp:
        # The configured roadmap and run folder may live under any name.
        # Reported paths stay repo-relative for --input phase_file.
        repo = Path(tmp).resolve()
        priv_map = repo / BOARD_REL / "roadmap"
        priv_wf = repo / BOARD_REL / "runs" / "phase-100010"
        priv_map.mkdir(parents=True)
        priv_wf.mkdir(parents=True)
        (priv_map / "phase-100010-alpha.md").write_text(
            "- [ ] Required approval is obtained.\n")
        (priv_map / "index.md").write_text("Plan ready\n")
        conventions.override({
            "roadmap-dir": BOARD_REL / "roadmap",
            "driver.runs": BOARD_REL / "runs",
        })
        assert RoadmapSource(repo).roadmap == priv_map
        assert runs_dir(repo) == priv_wf.parent, runs_dir(repo)
        got = select_next(repo, priv_map)
        assert got is not None and got[0] == 100010, got
        assert got[1].relative_to(repo) == (
            BOARD_REL / "roadmap" / "phase-100010-alpha.md"), got
    with tempfile.TemporaryDirectory() as tmp:
        # A named roadmap folder is read instead of the derived one, and a
        # folder that cannot be read is an error rather than a quiet fallback
        # to the folder that can. A machine pointed at a folder that does not
        # exist must hear about that folder, never look healthy on another one.
        # The repository is a subdirectory so a folder genuinely outside it can
        # be built beside it, inside this run's own temporary directory.
        repo = Path(tmp).resolve() / "repo"
        (repo / BOARD_REL / "roadmap").mkdir(parents=True)
        (repo / BOARD_REL / "runs").mkdir()
        named = repo / BOARD_REL / "elsewhere"
        named.mkdir()
        (named / "phase-100020-named.md").write_text("x\n")
        # Both folders carry an unchecked phase; naming one must decide the
        # answer, so the difference cannot come from anything else.
        for folder, number in ((repo / BOARD_REL / "roadmap", 100010), (named, 100020)):
            (folder / f"phase-{number:00d}-x.md").write_text(
                "- [ ] Required approval is obtained.\n")
        source = RoadmapSource(repo, named)
        assert source.roadmap == named, source.roadmap
        got = source.select_next()
        assert got is not None and got[0] == 100020, got
        # The same folder named relative to the repository root is the same
        # folder: cron starts the driver from an arbitrary directory, so a
        # relative name must not be read against the working directory.
        relative = RoadmapSource(repo, str(BOARD_REL / "elsewhere"))
        assert relative.roadmap == named, relative.roadmap
        # A missing named folder fails closed, and says which folder.
        try:
            RoadmapSource(repo, repo / BOARD_REL / "absent")
            refused = False
        except RoadmapUnavailable as exc:
            refused = True
            assert "absent" in str(exc), exc
        assert refused, "a missing named roadmap folder must be refused"
        # A folder outside the repository is allowed, because pointing a machine
        # at another checkout or another disk is the reason the setting exists.
        # Its phase files are then reported as absolute paths, since a launch
        # inside this checkout cannot name a path to somewhere else with a
        # relative one. Built inside this run's temporary directory so a rerun
        # cannot collide with the last run's leftovers.
        outside = Path(tmp).resolve() / "outside"
        outside.mkdir()
        (outside / "phase-100460-external.md").write_text("x\n")
        external = RoadmapSource(repo, outside)
        assert external.roadmap == outside, external.roadmap
        got = external.select_next()
        assert got is not None and got[0] == 100460, got
        assert reported_path(repo, got[1]) == str(outside / "phase-100460-external.md"), \
            reported_path(repo, got[1])
        # Inside the repository it is still reported relative, unchanged.
        assert reported_path(repo, named / "phase-100020-named.md") == str(
            BOARD_REL / "elsewhere" / "phase-100020-named.md"), \
            reported_path(repo, named / "phase-100020-named.md")
        # The file's value selects the roadmap when no CLI override is passed.
        assert RoadmapSource(repo).roadmap == repo / BOARD_REL / "roadmap"
    with tempfile.TemporaryDirectory() as tmp:
        class FakeStore:
            def __init__(self, private=None):
                self.state = {"version": 1, "phases": {}}
                self.reserved = []
                # A reservation token is written beside the store's own private
                # checkout, so a double needs to say which one that is. It is
                # resolved after the fixture root exists, below.
                self.private = private

            def read(self):
                return self.state, "fake"

            def reserve(self, phase, machine_id, **kwargs):
                record = {
                    "phase": f"{int(phase):06d}",
                    "machine_id": machine_id,
                    "reservation_id": f"rsv-{machine_id}",
                    "generation": 1,
                    "run_id": f"phase-{int(phase):06d}",
                }
                self.state["phases"][f"{int(phase):06d}"] = record
                self.reserved.append((int(phase), machine_id))
                return record

            def reserve_group(self, phases, machine_id, **kwargs):
                records = []
                for phase in phases:
                    key = f"{int(phase):06d}"
                    record = {
                        "phase": key,
                        "machine_id": machine_id,
                        "reservation_id": f"rsv-{machine_id}",
                        "generation": 1,
                        "run_id": f"phase-{key}",
                    }
                    self.state["phases"][key] = record
                    self.reserved.append((int(phase), machine_id))
                    records.append(record)
                return records

        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        for number in (100440, 100460, 100601):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text("x\\n")
        fake = FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(repo, "server-01", store=fake,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "RESERVED" and got["phase"] == "100440", got
        fake.state["phases"]["100440"]["status"] = "completed"
        (repo / BOARD_REL / "runs" / "phase-100440").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100440" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        fake.state["phases"]["100601"] = {
            "status": "claimed", "machine_id": "local-01",
            "reservation_id": "rsv-local", "generation": 1,
            "run_id": "phase-100601",
        }
        got = select_server_phase(repo, "server-01", store=fake,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "RESERVED" and got["phase"] == "100460", got
        fake.state["phases"]["100460"]["status"] = "completed"
        (repo / BOARD_REL / "runs" / "phase-100460").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100460" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        got = select_server_phase(repo, "server-01", store=fake, reserve=False,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "WAIT_FOR_CLAIM" and got["phase"] == "100601", got

        # An operator close-out ticks the approval box but cannot produce a
        # published receipt, so it leaves a live claim behind. A phase that is
        # finished on disk must not gate the line on that stale record: the
        # selector moves past it to the next unfinished phase.
        #
        # The phase carries a rejected run record, because that is what makes
        # the tick count. A tick on a phase nothing ever ran no longer retires
        # it: the box alone used to be enough, and a phase could be marked
        # finished with no run, no ledger and no receipt anywhere.
        (repo / "roadmap" / "phase-100620-synthetic.md").write_text("x\\n")
        (repo / BOARD_REL / "runs" / "phase-100601").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100601" / "run.json").write_text(
            json.dumps({"state": "rejected"}))
        p601 = repo / "roadmap" / "phase-100601-synthetic.md"
        p601.write_text("- [x] Required approval is obtained\n")
        got = select_server_phase(repo, "server-01", store=fake, reserve=False,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "AVAILABLE" and got["phase"] == "100620", got
        p601.write_text("x\n")

        # The fail-closed half is unchanged: coordination asserting
        # "completed" with no completion evidence anywhere is still an
        # inconsistency, not a silent pass.
        (repo / BOARD_REL / "runs" / "phase-100601").mkdir(parents=True, exist_ok=True)
        fake.state["phases"]["100601"]["status"] = "completed"
        got = select_server_phase(repo, "server-01", store=fake, reserve=False,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "COORDINATION_INCONSISTENT", got

        class FailingStore(FakeStore):
            def reserve(self, phase, machine_id, **kwargs):
                raise CoordinationError("coordination push failed")

        try:
            select_server_phase(repo, "server-01", store=FailingStore(),
                                roadmap=repo / "roadmap")
            launch_refused = False
        except CoordinationError:
            launch_refused = True
        assert launch_refused
    with tempfile.TemporaryDirectory() as tmp:
        # A phase that declares a reservation group is selected as one unit:
        # its unfinished remainder is claimed under one shared reservation id
        # and reported as an array. A phase with no declaration still selects
        # alone through the same path, which is the backward-compatible half.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        together = ("### Reservation Group\n\n"
                    "`together: 100010, 100020, 100030`\n")
        for number in (100010, 100020, 100030):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text(
                together)
        fake = FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(repo, "server-01", store=fake,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "RESERVED", got
        assert got["group"] == ["100010", "100020", "100030"], got
        assert [member["phase"] for member in got["members"]] == \
            ["100010", "100020", "100030"], got
        assert len({member["reservation_id"]
                    for member in got["members"]}) == 1, got
        for member in got["members"]:
            token = json.loads(
                reservation_file(repo, member["phase"]).read_text())
            assert token["reservation_id"] == member["reservation_id"], token
        # The whole group is held now, so the same selection waits for it.
        got = select_server_phase(repo, "server-01", store=fake, reserve=False,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "WAIT_FOR_CLAIM", got
    with tempfile.TemporaryDirectory() as tmp:
        # A single phase that declares no group is still selected alone and
        # reported without a group array: the single-phase contract is intact.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        (repo / "roadmap" / "phase-100010-plain.md").write_text(
            "## 1. Objective\n")
        fake = FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(repo, "server-01", store=fake,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "RESERVED" and got["phase"] == "100010", got
        assert "members" not in got and "group" not in got, got
    with tempfile.TemporaryDirectory() as tmp:
        # A run that ended rejected or remedy_escalated is neither done nor
        # offerable. The selector reports it under needs_decision and moves on,
        # instead of offering a phase the driver would recover and launch into
        # glide's "already finished" refusal, halting the line on every tick.
        # This is the phase-100878 wedge (issue #56).
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL / "runs").mkdir(parents=True)
        for number, state in ((100010, "rejected"),
                              (100020, "remedy_escalated"),
                              (100030, "completed")):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text("x\n")
            run_dir = repo / BOARD_REL / "runs" / f"phase-{number:06d}"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"state": state}))

        class EmptyStore:
            def __init__(self, private):
                self.private = private

            def read(self):
                return {"phases": {}}, "fake"

        got = select_server_phase(repo, "server-01",
                                  store=EmptyStore(private=repo / BOARD_REL),
                                  reserve=False, roadmap=repo / "roadmap")
        assert got["state"] == "NO_PHASE", got
        skipped = got.get("needs_decision") or []
        assert [e["phase"] for e in skipped] == ["100010", "100020"], skipped
        assert [e["state"] for e in skipped] == ["rejected", "remedy_escalated"], skipped
        assert needs_decision(repo, 100010) == "rejected"
        assert needs_decision(repo, 100030) is None
        assert signals(
            repo, 100020, repo / "roadmap" / "phase-100020-synthetic.md",
            set())["needs_decision"] is True
    with tempfile.TemporaryDirectory() as tmp:
        # An open decision gate stops the selector before it reserves. This is
        # the 100817 regression: the gate was real, the developer stopped on it
        # mid-run, and a reservation plus a branch were spent getting there.
        # Assert both halves: the open gate is refused, and recording the
        # choice makes the very same phase selectable with no other change.
        class FakeGateStore:
            def __init__(self, private=None):
                self.state = {"version": 1, "phases": {}}
                self.reserved = []
                self.private = private

            def read(self):
                return self.state, "fake"

            def reserve(self, phase, machine_id, **kwargs):
                record = {
                    "phase": f"{int(phase):06d}",
                    "machine_id": machine_id,
                    "reservation_id": f"rsv-{machine_id}",
                    "generation": 1,
                    "run_id": f"phase-{int(phase):06d}",
                }
                self.state["phases"][f"{int(phase):06d}"] = record
                self.reserved.append(int(phase))
                return record

        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        gate_plan = repo / "roadmap" / "phase-100440-gated.md"
        gate_plan.write_text(
            "## 1. Objective\n"
            "### Sources\n"
            "| Issue | Subject | Kind |\n"
            "|---|---|---|\n"
            "| [#1](x) | something | Security |\n"
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `open` | — | — |\n")
        store = FakeGateStore(private=repo / BOARD_REL)
        got = select_server_phase(repo, "server-01", store=store,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "BLOCKED_ON_GATE", got
        assert got["phase"] == "100440", got
        assert store.reserved == [], "an open gate must not reserve a phase"

        # Same file, decision recorded: the phase is selectable, and only the
        # recorded choice is what changed.
        gate_plan.write_text(
            "## 1. Objective\n"
            "### Sources\n"
            "| Issue | Subject | Kind |\n"
            "|---|---|---|\n"
            "| [#1](x) | something | Security |\n"
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `recorded` 2026-09-28 "
            "| Option 1 | Option 2 |\n")
        got = select_server_phase(repo, "server-01", store=store,
                                  roadmap=repo / "roadmap")
        assert got["state"] == "RESERVED" and got["phase"] == "100440", got
        assert store.reserved == [100440], store.reserved

        # A gate section that claims `recorded` with no choice is a defect in
        # the plan, and the selector fails closed on it rather than launching.
        gate_plan.write_text(
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `recorded` 2026-09-28 | — | — |\n")
        assert gate_block(100440, gate_plan) is not None, \
            "recorded with an empty choice must fail closed"
    with tempfile.TemporaryDirectory() as tmp:
        # Exercise the real Git-backed store through the selector rather than
        # relying only on FakeStore for cross-machine ordering.
        from argus.phase_reservations import _git_test_repo, _self_test_receipt
        remote, clients = _git_test_repo(Path(tmp).resolve(), "real-ordering")
        repo = Path(tmp).resolve() / "root"
        nested = repo / BOARD_REL
        (nested / "roadmap").mkdir(parents=True)
        (nested / "runs").mkdir()
        (nested / "roadmap" / "index.md").write_text("Plan ready\n")
        for number in (100440, 100460, 100601, 100602):
            (nested / "roadmap" / f"phase-{number}-synthetic.md").write_text("x\n")
        roadmap = nested / "roadmap"
        store_server = ReservationStore(repo, private_repo=clients[0])
        store_local = ReservationStore(repo, private_repo=clients[1])
        first = select_server_phase(repo, "server-01", store=store_server,
                                    roadmap=roadmap)
        assert first["state"] == "RESERVED" and first["phase"] == "100440", first
        local = store_local.reserve(100601, "local-01")
        (nested / "runs" / "phase-100601").mkdir()
        (nested / "runs" / "phase-100601" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        old_runs = conventions.override({
            "driver.runs": store_local.private / "runs"})
        try:
            local_receipt = _self_test_receipt(store_local, local)
            store_local.set_status(local, "completed", evidence_path=local_receipt)
        finally:
            conventions.configure(old_runs)
        server = dict(store_server.read()[0]["phases"]["100440"])
        server["phase"] = "100440"
        (nested / "runs" / "phase-100440").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100440" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        old_runs = conventions.override({
            "driver.runs": store_server.private / "runs"})
        try:
            server_receipt = _self_test_receipt(store_server, server)
            store_server.set_status(server, "completed", evidence_path=server_receipt)
        finally:
            conventions.configure(old_runs)
        following = select_server_phase(repo, "server-01", store=store_server,
                                        roadmap=roadmap)
        assert following["state"] == "RESERVED" and following["phase"] == "100460", following
        following_record = dict(store_server.read()[0]["phases"]["100460"])
        following_record["phase"] = "100460"
        (nested / "runs" / "phase-100460").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100460" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        old_runs = conventions.override({
            "driver.runs": store_server.private / "runs"})
        try:
            following_receipt = _self_test_receipt(
                store_server, following_record)
            store_server.set_status(
                following_record, "completed", evidence_path=following_receipt)
        finally:
            conventions.configure(old_runs)
        store_local.reserve(100602, "local-01")
        waiting = select_server_phase(repo, "server-01", store=store_server,
                                      roadmap=roadmap)
        assert waiting["state"] == "WAIT_FOR_CLAIM" and waiting["phase"] == "100602", waiting

        # A phase whose pull request is open is finished work, not queue work.
        # It is never offered for another run, and the line no longer stops
        # behind it: it is skipped, reported under awaiting_review so the driver
        # can still run the merge check, and the next phase is offered. The old
        # behaviour waited here for a human, which meant an abandoned pull
        # request parked every later phase with no way back.
        review_phase = 100590
        review_file = (roadmap / f"phase-{review_phase}-awaiting-review.md")
        review_file.write_text("- [ ] Required approval is obtained.\n")
        (nested / "runs" / f"phase-{review_phase}").mkdir(parents=True,
                                                          exist_ok=True)
        (nested / "runs" / f"phase-{review_phase}" / "publication.json").write_text(
            json.dumps({"version": 1, "phase": f"{review_phase:06d}",
                        "state": "awaiting_review",
                        "branch": "feature/awaiting-review",
                        "pull_request": "https://example/pr/9",
                        "public_head": "0" * 40,
                        "private_head": "0" * 40}))
        later_file = roadmap / "phase-100591-after-review.md"
        later_file.write_text("- [ ] Required approval is obtained.\n")
        review = select_server_phase(repo, "server-01", store=store_server,
                                     roadmap=roadmap)
        # The line moves past the open pull request...
        assert review["state"] == "RESERVED" and review["phase"] == "100591", review
        # ...and says which phase it moved past, or nothing would ever run the
        # merge check on it and its receipt would read awaiting_review for ever.
        skipped = review.get("awaiting_review") or []
        assert [e["phase"] for e in skipped] == [f"{review_phase:06d}"], review
        assert skipped[0]["branch"].endswith("awaiting-review"), review
        assert skipped[0]["pull_request"] == "https://example/pr/9", review
        # It is not done, so a plain "is this finished" must also say no. The
        # skip is about the queue, not about the phase's completion.
        assert not is_done(repo, review_phase, review_file, set())
        # And it must never be handed out for a run of its own.
        offered = select_server_phase(repo, "server-01", store=store_server,
                                      reserve=False, roadmap=roadmap)
        assert offered["phase"] == "100591", offered
        # The skipped phase rides along on an outcome that does not launch
        # either. That is the case that matters: a tick whose selection is
        # WAIT_FOR_CLAIM is still the tick on which a merge check is due, so
        # dropping the report there would strand the merged phase's receipt.
        drained_record = dict(store_server.read()[0]["phases"]["100591"])
        drained_record["phase"] = "100591"
        (nested / "runs" / "phase-100591").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100591" / "run.json").write_text(
            json.dumps({"state": "completed"}))
        old_runs = conventions.override({
            "driver.runs": store_server.private / "runs"})
        try:
            store_server.set_status(
                drained_record, "completed",
                evidence_path=_self_test_receipt(store_server, drained_record))
        finally:
            conventions.configure(old_runs)
        drained = select_server_phase(repo, "server-01", store=store_server,
                                      roadmap=roadmap)
        assert [e["phase"] for e in (drained.get("awaiting_review") or [])] \
            == [f"{review_phase:06d}"], drained
        # And on the one outcome that is not a launch at all.
        drained_state = select_server_phase(repo, "server-01", store=store_server,
                                            reserve=False, roadmap=roadmap)
        assert [e["phase"] for e in (drained_state.get("awaiting_review") or [])] \
            == [f"{review_phase:06d}"], drained_state

    with tempfile.TemporaryDirectory() as tmp:
        # The config names the board and run folder. A store that was given a
        # board explicitly keeps that board, while its token uses the configured
        # run folder.
        from argus.phase_reservations import _git_test_repo

        case = Path(tmp).resolve()
        decoy = _git_test_repo(case, "decoy")[1][0]
        repo = case / "root"
        nested = repo / BOARD_REL
        (nested / "roadmap").mkdir(parents=True)
        (nested / "runs").mkdir()
        (nested / "roadmap" / "index.md").write_text("Plan ready\n")
        number = 100777
        (nested / "roadmap" / f"phase-{number}-hermetic.md").write_text("x\n")
        own_private = _git_test_repo(case, "hermetic")[1][0]
        (own_private / "runs").mkdir(exist_ok=True)
        conventions.override({
            "repo-root": repo,
            "roadmap-dir": BOARD_REL / "roadmap",
            "driver.runs": BOARD_REL / "runs",
            "board.repo": decoy,
        })
        assert conventions.board_repo(repo) == decoy.resolve()
        store = ReservationStore(repo, private_repo=own_private)
        assert store.private == own_private.resolve(), store.private
        got = select_server_phase(repo, "server-01", store=store,
                                  roadmap=nested / "roadmap")
        assert got["state"] == "RESERVED" and got["phase"] == str(number), got
        assert not (decoy / "runs" / f"phase-{number}"
                    / "reservation.json").exists()
        assert (nested / "runs" / f"phase-{number}"
                / "reservation.json").exists()

    # The class the registry hands the driver must reach the same answer as the
    # functions, or the driver is being served by a different code path than
    # the one this file's tests above cover.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        (repo / "roadmap" / "phase-100440-first.md").write_text("x\n")

        class ReadOnlyStore:
            def __init__(self):
                self.state = {"version": 1, "phases": {}}

            def read(self):
                return self.state, "fake"

        source = RoadmapSource(repo, repo / "roadmap")
        got = source.select("server-01", reserve=False, store=ReadOnlyStore())
        assert got["state"] == "AVAILABLE" and got["phase"] == "100440", got
        assert source.select_next()[0] == 100440
        assert [n for n, _ in source.candidates()] == [100440]
        assert source.signals((100440, repo / "roadmap" / "phase-100440-first.md"))

    _self_test_publisher_lookup()


def _self_test_publisher_lookup():
    from argus.gitsync import awaiting_review
    assert _gitsync(Path(".")) is awaiting_review
