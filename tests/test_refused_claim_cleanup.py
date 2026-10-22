# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A refused driver fence returns only the reservation it selected."""

import json
import shlex
from pathlib import Path

import pytest

from argus import conventions
from argus.board.reservations import ReservationStore
from driver_maintenance_support import shell
from driver_publication_fixture import DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)

NUMBER = 100449


@pytest.mark.parametrize("kind", ["phase", "issue"])
@pytest.mark.parametrize(
    "replacement", [False, True], ids=["write-refusal", "new-owner"]
)
def test_refused_tick_returns_only_its_selected_claim(
    driver_publication: DriverPublication, tmp_path: Path, kind: str, replacement: bool
) -> None:
    fixture = driver_publication
    config = json.loads(fixture.config.read_text())
    config["board"]["issues-file"] = "issues.json"
    fixture.config.write_text(json.dumps(config))
    conventions.configure_from_cli(["--config", str(fixture.config)])
    store = ReservationStore(fixture.root, issues=kind == "issue")
    token = store.reserve(NUMBER, "server-01")
    if replacement:
        store.release_group(token)
        store.reserve(NUMBER, "server-02")
    harness = tmp_path / "tick-harness"
    harness.mkdir()
    output = shell(
        harness,
        f"REPO={shlex.quote(str(fixture.root))}\n"
        f"ARGUS_CONFIG_ARGS=(--config {shlex.quote(str(fixture.config))})\n"
        f"work_kind={kind}; BOARD_KIND_ARGS=({'--issues' if kind == 'issue' else ''})\n"
        "PHASE_MACHINE_ID=server-01\n"
        'CLAIM="$RUN_DIR/missing/claim"\n'
        "reconcile_pending_reviews() { PENDING_PUBLISHED=0; }\n"
        'board_assert_owner() { board_assert_owner_cmd "$@"; }\n'
        "_drive_choose_selection() {\n"
        f"  number={NUMBER}; rel=fixture.md; reservation_id={token['reservation_id']}\n"
        f"  reservation_generation={token['generation']}; _DRIVE_RC=''\n"
        "}\n"
        'rc=0; _drive || rc=$?; printf "tick_rc=%s\\n" "$rc"\n',
    )

    assert "tick_rc=1" in output
    record = store.read()[0]["phases"].get(str(NUMBER))
    if replacement:
        assert record is not None
        assert record["machine_id"] == "server-02"
    else:
        assert record is None
    evidence = harness / f"claim-refusal-release-{NUMBER}.json"
    assert evidence.is_file()
    if replacement:
        assert json.loads(evidence.read_text())["state"] == "FENCED"
    events = (harness / "events").read_text()
    assert "BRANCH" not in events
    assert "LAUNCH" not in events
