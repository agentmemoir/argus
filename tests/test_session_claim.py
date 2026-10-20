# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""`--session` holds a live claim before it starts the work, or it refuses."""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argus.board.core import _iso, _Mutation
from argus.board.store import ReservationStore
from argus.board.worktree import _transaction
from driver_publication_fixture import DRIVER, DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)

FREE = 100446
OTHER = 100447


def add_phase(fixture: DriverPublication, number: int, name: str) -> str:
    """Add a phase document to the fixture checkout and return its relative path."""
    folder = fixture.root / "phases-data" / "phases"
    path = folder / f"phase-{number}-{name}.md"
    path.write_text(f"# {number}. {name}\n\n- [ ] Work to do.\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(folder), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(folder), "commit", "-qm", name],
        check=True,
        capture_output=True,
    )
    return f"phases-data/phases/{path.name}"


def session(
    fixture: DriverPublication, number: int, rel: str, **env: str
) -> subprocess.CompletedProcess[str]:
    base = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DRIVER_", "CLIO_", "ARGUS_"))
    }
    base.update(DRIVER_STUB_RC="0", DRIVER_STUB_SLEEP="0", **env)
    return subprocess.run(
        [
            "bash",
            str(DRIVER),
            "--config",
            str(fixture.config),
            "--session",
            str(number),
            rel,
        ],
        cwd=fixture.root,
        env=base,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def windows(fixture: DriverPublication) -> list[list[str]]:
    return [row for row in fixture.commands() if row[1] == "new-window"]


def patch_record(
    fixture: DriverPublication,
    number: int,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    """Change one board record through the store's own write transaction."""
    store = ReservationStore(fixture.root)

    def mutate(state: dict[str, Any]) -> _Mutation:
        record = state["phases"][str(number)]
        change(record)
        return _Mutation(True, None)

    _transaction(
        store.private, store.remote, store.branch, mutate, message, issues=False
    )


def test_session_on_a_free_phase_claims_it_and_records_the_claim(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, FREE, "free")
    (fixture.state / "claim").unlink()
    result = session(fixture, FREE, rel)
    assert result.returncode == 0, result.stderr
    claim = (fixture.state / "claim").read_text().split("\t")
    assert claim[0] == str(FREE)
    record = ReservationStore(fixture.root).read()[0]["phases"][str(FREE)]
    assert claim[1] == record["reservation_id"]
    assert claim[2].strip() == str(record["generation"])
    assert record["machine_id"] == "server-01"
    # The run marks the claim running just before the work starts.
    assert record["status"] == "running"


def test_session_refuses_a_phase_another_machine_holds(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "held")
    (fixture.state / "claim").unlink()
    ReservationStore(fixture.root).reserve(OTHER, "other-machine")
    before = len(windows(fixture))
    result = session(fixture, OTHER, rel)
    assert result.returncode != 0
    assert "held by another machine" in result.stderr
    assert "other-machine" not in result.stderr
    assert not (fixture.state / "claim").exists()
    assert len(windows(fixture)) == before


def test_session_refuses_a_stale_parent_fence(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "stale")
    (fixture.state / "claim").unlink()
    token = ReservationStore(fixture.root).reserve(OTHER, "server-01")
    before = len(windows(fixture))
    result = session(
        fixture,
        OTHER,
        rel,
        CLIO_RESERVATION_ID=str(token["reservation_id"]),
        CLIO_RESERVATION_GENERATION=str(token["generation"] + 1),
    )
    assert result.returncode != 0
    assert "stale" in result.stderr
    assert not (fixture.state / "claim").exists()
    assert len(windows(fixture)) == before


def test_session_refuses_an_empty_machine_id(tmp_path: Path) -> None:
    """The gate refuses before it reads the board, with no machine id at all."""
    driver = Path(__file__).resolve().parents[1] / "src/argus/driver"
    script = f"""
set -euo pipefail
. "{driver}/lib-driver-session-claim.sh"
PHASE_MACHINE_ID=""
ARGUS="$TEST_ROOT"; REPO="$TEST_ROOT"
ARGUS_CONFIG_ARGS=(); BOARD_KIND_ARGS=()
PYTHON="{sys.executable}"
CLAIM="$TEST_ROOT/claim"
record_claim() {{ printf 'recorded\\n' >"$CLAIM"; }}
rc=0
session_claim_ensure 100446 phases/phase-100446-empty.md 2>"$TEST_ROOT/err" || rc=$?
printf 'rc=%s\\n' "$rc"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_ROOT": str(tmp_path),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "rc=1" in result.stdout
    assert "machine id is empty" in (tmp_path / "err").read_text()
    assert not (tmp_path / "claim").exists()


def test_session_reuses_this_machines_live_claim(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "own")
    (fixture.state / "claim").unlink()
    token = ReservationStore(fixture.root).reserve(OTHER, "server-01")
    result = session(fixture, OTHER, rel)
    assert result.returncode == 0, result.stderr
    claim = (fixture.state / "claim").read_text().split("\t")
    assert claim[0] == str(OTHER)
    record = ReservationStore(fixture.root).read()[0]["phases"][str(OTHER)]
    # Reuse, not a second reservation: the id the board already held is the id
    # the run wrote, and the owner never changed.
    assert claim[1] == token["reservation_id"] == record["reservation_id"]
    assert record["machine_id"] == "server-01"
    assert record["status"] == "running"


def test_session_refuses_to_reuse_a_claim_past_its_window(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "stale-window")
    (fixture.state / "claim").unlink()
    token = ReservationStore(fixture.root).reserve(OTHER, "server-01")
    patch_record(
        fixture,
        OTHER,
        lambda record: record.__setitem__(
            "claimed_at", _iso(datetime.now(UTC) - timedelta(hours=49))
        ),
        "test: a claim whose start window has passed",
    )
    before = len(windows(fixture))
    result = session(fixture, OTHER, rel)
    assert result.returncode != 0
    assert "no launch" in result.stderr
    assert not (fixture.state / "claim").exists()
    assert len(windows(fixture)) == before
    record = ReservationStore(fixture.root).read()[0]["phases"][str(OTHER)]
    # The stale claim was not reused and nothing new was minted over it.
    assert record["reservation_id"] == token["reservation_id"]


def test_session_refuses_a_completed_phase_at_the_gate(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "done")
    (fixture.state / "claim").unlink()
    token = ReservationStore(fixture.root).reserve(OTHER, "server-01")
    patch_record(
        fixture,
        OTHER,
        lambda record: record.__setitem__("status", "completed"),
        "test: a phase the board already finished",
    )
    before = len(windows(fixture))
    result = session(
        fixture,
        OTHER,
        rel,
        CLIO_RESERVATION_ID=str(token["reservation_id"]),
        CLIO_RESERVATION_GENERATION=str(token["generation"]),
    )
    assert result.returncode != 0
    assert "already completed; no launch" in result.stderr
    assert not (fixture.state / "claim").exists()
    assert len(windows(fixture)) == before


def test_session_refuses_an_expired_record_at_the_gate(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_phase(fixture, OTHER, "expired")
    (fixture.state / "claim").unlink()
    token = ReservationStore(fixture.root).reserve(OTHER, "server-01")
    ReservationStore(fixture.root).expire_stale(
        now=datetime.now(UTC) + timedelta(hours=49)
    )
    before = len(windows(fixture))
    result = session(
        fixture,
        OTHER,
        rel,
        CLIO_RESERVATION_ID=str(token["reservation_id"]),
        CLIO_RESERVATION_GENERATION=str(token["generation"]),
    )
    assert result.returncode != 0
    assert "no launch" in result.stderr
    assert not (fixture.state / "claim").exists()
    assert len(windows(fixture)) == before


def add_group_phase(fixture: DriverPublication, number: int, members: list[int]) -> str:
    """Add a phase document that declares a reservation group."""
    rel = add_phase(fixture, number, f"member-{number}")
    path = fixture.root / rel
    listed = ", ".join(str(member) for member in members)
    path.write_text(
        f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
        f"### Reservation Group\n\n`together: {listed}`\n",
        encoding="utf-8",
    )
    folder = fixture.root / "phases-data"
    subprocess.run(
        ["git", "-C", str(folder), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(folder), "commit", "-qm", f"group {number}"],
        check=True,
        capture_output=True,
    )
    return rel


def test_session_reuse_claims_the_declared_group(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    rel = add_group_phase(fixture, OTHER, [OTHER, OTHER + 1])
    add_group_phase(fixture, OTHER + 1, [OTHER, OTHER + 1])
    (fixture.state / "claim").unlink()
    tokens = ReservationStore(fixture.root).reserve_group(
        [OTHER, OTHER + 1], "server-01"
    )
    result = session(fixture, OTHER, rel)
    assert result.returncode == 0, result.stderr
    claim = (fixture.state / "claim").read_text().split("\t")
    record = ReservationStore(fixture.root).read()[0]["phases"][str(OTHER)]
    # The reused hold is the same group reservation, not a fresh one.
    assert claim[1] == tokens[0]["reservation_id"] == record["reservation_id"]
    assert record["status"] == "running"
