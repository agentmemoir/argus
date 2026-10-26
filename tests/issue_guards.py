# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run only the driver or board guard that a fault changes."""

import sys
from pathlib import Path

import pytest


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "src"))
    cases = {
        "preflight": (
            "test_issue_driver.py::test_issue_preflight_refuses_before_selection",
            "issue preflight guard failed",
        ),
        "namespace": (
            "test_issue_board.py::test_phase_and_issue_claims_do_not_share_records",
            "issue board namespace guard failed",
        ),
    }
    target, failure = cases[sys.argv[1]]
    rc = pytest.main(["-q", str(repo / "tests" / target)])
    if rc:
        print("FAIL: " + failure)
    print("issue guards self-test: " + ("FAIL" if rc else "PASS"))
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
