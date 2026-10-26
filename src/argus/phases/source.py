#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The phases work source: work comes from phase files in a phases folder.

This is the one work source that exists. It reads the phase files out of a
phases folder and returns the next uncompleted one. The driver never learns
which folder that is, or even that the work came from a phases; it asks the
configured source and gets back the result shape the driver already reads.

Phases files live in the configured phases folder. The pipeline owns
its run directory; standalone tools use the configured runs folder. A
machine may name a different phases folder; see ``resolve_phases``.

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
  * phases/index.md marks it Complete (row status or
    bold heading).

The index.md source exists because phase templates drift: some files never
carry the approval box. When index.md says Complete but no other marker is
present, we warn on stderr and still treat it as done, so a completed phase is
never silently re-run.

Appendix files (phase-*-appendix-*.md) are references, never runnable phases.
Parked phases (number >= 900000) are phases-only and are ignored by selection,
audit, and verbose output.

A phase whose pull request is open but unmerged is finished work, not queue
work. Its receipt records ``awaiting_review``, which is not one of the
completion markers above, so the phase is not done; the server selection
returns it under ``awaiting_review`` so the driver can still run the merge
check, which is the only thing that turns a finished pull request into a
finished phase.
"""

if not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus.phases.evidence import (  # noqa: E402 - path set just above
    APPROVAL,
    INDEX_HEAD_RE,
    INDEX_ROW_RE,
    NEEDS_DECISION_RUN_STATES,
    PhasesUnavailable,
    file_approved,
    finalize_log_done,
    index_complete,
    is_done,
    ledger_finalized,
    ledger_present,
    ledger_steps,
    needs_decision,
    phase_attempted,
    publication_receipt_done,
    reported_path,
    resolve_phases,
    run_completed,
    run_dir,
    signals,
)
from argus.phases.group import (  # noqa: E402 - path set just above
    GROUP_HEADING,
    GROUP_HEADING_RE,
    GROUP_TOGETHER_RE,
    GROUP_TOKEN_RE,
    ReservationGroupError,
    ReservationGroupMissing,
    declared_group_members,
    resolve_group,
    resolve_group_or_none,
)
from argus.phases.index import audit  # noqa: E402 - path set just above
from argus.phases.queue import (  # noqa: E402 - path set just above
    candidates,
    select_next,
)
from argus.phases.selftest import self_test  # noqa: E402 - path set just above
from argus.phases.selftest_fixture import BOARD_REL  # noqa: E402 - path set just above
from argus.phases.server import (  # noqa: E402 - path set just above
    PhasesSource,
    select_server_phase,
)

# The module surface every caller and script relied on.
__all__ = [
    "APPROVAL",
    "BOARD_REL",
    "GROUP_HEADING",
    "GROUP_HEADING_RE",
    "GROUP_TOGETHER_RE",
    "GROUP_TOKEN_RE",
    "INDEX_HEAD_RE",
    "INDEX_ROW_RE",
    "NEEDS_DECISION_RUN_STATES",
    "ReservationGroupError",
    "ReservationGroupMissing",
    "PhasesSource",
    "PhasesUnavailable",
    "audit",
    "candidates",
    "declared_group_members",
    "file_approved",
    "finalize_log_done",
    "index_complete",
    "is_done",
    "ledger_finalized",
    "ledger_present",
    "ledger_steps",
    "needs_decision",
    "phase_attempted",
    "publication_receipt_done",
    "reported_path",
    "resolve_group",
    "resolve_group_or_none",
    "resolve_phases",
    "run_completed",
    "run_dir",
    "select_next",
    "select_server_phase",
    "self_test",
    "signals",
]
