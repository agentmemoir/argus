# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Small publication fixtures used by the group self-tests."""

import json
from pathlib import Path
from typing import Any

from argus.gitsync.selftest_fixture import BOARD_REL, _configure_self_test


def receipt_fixture(base: Path, name: str) -> Path:
    """Configure a checkout whose publication state comes only from receipts."""
    pub = base / name / "pub"
    pub.mkdir(parents=True)
    _configure_self_test(pub, pub / BOARD_REL)
    return pub


def write_receipt(pub: Path, number: int, **fields: Any) -> None:
    run_dir = pub / BOARD_REL / "runs" / f"phase-{number:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "publication.json").write_text(
        json.dumps({"phase": f"{number:06d}", **fields}) + "\n"
    )
