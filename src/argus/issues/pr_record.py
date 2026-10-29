# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Record an issue's pull request on the claim its run holds, when the pull request opens."""

import os
import sys
from pathlib import Path

from argus import conventions
from argus.board.core import CoordinationError
from argus.board.reservations import ReservationStore
from argus.issues.client import BRANCH_RE
from argus.issues.transport import IssuesUnavailable


def record_pull_request(root: Path, branch: str, url: str) -> bool:
    """Write the branch and URL to the issue's board record under this run's claim.

    The claim token comes from the driver's environment. A run without a token
    writes nothing, and the tick writes the record later instead. A refused write
    is logged and returns False, so the publication still completes.
    """
    match = BRANCH_RE.fullmatch(branch)
    reservation = os.environ.get("CLIO_RESERVATION_ID", "")
    generation = os.environ.get("CLIO_RESERVATION_GENERATION", "")
    machine = conventions.setting("machine.id", None)
    if match is None or not reservation or not generation.isdigit() or not machine:
        return False
    number = int(match.group(1))
    token = {
        "issue": str(number),
        "machine_id": machine,
        "reservation_id": reservation,
        "generation": int(generation),
    }
    try:
        store = ReservationStore(root, issues=True)
        store.set_issue_pull_request(token, {"branch": branch, "url": url})
    except (CoordinationError, IssuesUnavailable, OSError, ValueError) as exc:
        print(
            f"issue {number}: pull request not recorded on the board: {exc}",
            file=sys.stderr,
        )
        return False
    return True
