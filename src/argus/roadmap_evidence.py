# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Completion evidence for one phase item, and the folder it is read from.

A phase is done when the pipeline's own records say so, so this module reads
them: the run record, the ledger, the publication receipt, the finalize log and
the roadmap index. The approval box corroborates and never completes on its own,
and a phase the index calls Complete with no marker at all still counts as done,
with a warning, so a finished phase is never silently re-run.

The roadmap folder a machine was told to read is resolved here too, and a phase
file is reported here the way the driver must launch it.
"""

import json
import re
import sys
from pathlib import Path

from argus import conventions
from argus.conventions import phase_run_dir
from argus.phase_policy import is_runnable_phase
from argus.work_source import WorkSourceError

INDEX_ROW_RE = re.compile(r"^\|\s*(\d{4,6})\s*\|")
INDEX_HEAD_RE = re.compile(r"^#{2,3}\s*(\d{4,6})\.\s")
APPROVAL = "Required approval is obtained"


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
            f"configured roadmap folder {base} is missing or is not a directory"
        )
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
        ledger = json.loads(
            (run_dir(repo, number, phase_file) / "ledger.json").read_text()
        )
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
    return (
        isinstance(data, dict)
        and data.get("version") == 1
        and data.get("state") == "published"
        and data.get("phase") == f"{int(number):06d}"
    )


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


def index_complete(_repo, roadmap):
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
    if (
        run_completed(repo, number, path)
        or (file_approved(path) and phase_attempted(repo, number, path))
        or publication_receipt_done(repo, number, path)
    ):
        return True
    if ledger_present(repo, number, path):
        # The ledger is runner-maintained and re-verified on resume; when it
        # parses, it outranks the raw agent-written finalize log, so a stale
        # DONE log cannot override a ledger with no finalize entry.
        return ledger_finalized(repo, number, path)
    if finalize_log_done(repo, number, path):
        return True
    if number in index_done:
        print(
            f"warning: phase {number} is marked Complete in index.md but has "
            f"no run.json/ledger/publication/finalize-log marker; treating it "
            f"as done",
            file=sys.stderr,
        )
        return True
    return False


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
