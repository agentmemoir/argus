# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test cases for the real board: two clients, one remote, one branch."""

import json
import tempfile
from pathlib import Path

from argus import conventions
from argus.board.reservations import ReservationStore
from argus.roadmap.evidence import is_done
from argus.roadmap.selftest_fixture import BOARD_REL
from argus.roadmap.server import select_server_phase


def _board_checks():
    """The board a machine really has: real checkouts and real pushes."""
    with tempfile.TemporaryDirectory() as tmp:
        # Exercise the real Git-backed store through the selector rather than
        # relying only on FakeStore for cross-machine ordering.
        from argus.board.reservations import _git_test_repo, _self_test_receipt

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
        first = select_server_phase(
            repo, "server-01", store=store_server, roadmap=roadmap
        )
        assert first["state"] == "RESERVED" and first["phase"] == "100440", first
        local = store_local.reserve(100601, "local-01")
        (nested / "runs" / "phase-100601").mkdir()
        (nested / "runs" / "phase-100601" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        old_runs = conventions.override({"driver.runs": store_local.private / "runs"})
        try:
            local_receipt = _self_test_receipt(store_local, local)
            store_local.set_status(local, "completed", evidence_path=local_receipt)
        finally:
            conventions.configure(old_runs)
        server = dict(store_server.read()[0]["phases"]["100440"])
        server["phase"] = "100440"
        (nested / "runs" / "phase-100440").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100440" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        old_runs = conventions.override({"driver.runs": store_server.private / "runs"})
        try:
            server_receipt = _self_test_receipt(store_server, server)
            store_server.set_status(server, "completed", evidence_path=server_receipt)
        finally:
            conventions.configure(old_runs)
        following = select_server_phase(
            repo, "server-01", store=store_server, roadmap=roadmap
        )
        assert following["state"] == "RESERVED" and following["phase"] == "100460", (
            following
        )
        following_record = dict(store_server.read()[0]["phases"]["100460"])
        following_record["phase"] = "100460"
        (nested / "runs" / "phase-100460").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100460" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        old_runs = conventions.override({"driver.runs": store_server.private / "runs"})
        try:
            following_receipt = _self_test_receipt(store_server, following_record)
            store_server.set_status(
                following_record, "completed", evidence_path=following_receipt
            )
        finally:
            conventions.configure(old_runs)
        store_local.reserve(100602, "local-01")
        waiting = select_server_phase(
            repo, "server-01", store=store_server, roadmap=roadmap
        )
        assert waiting["state"] == "WAIT_FOR_CLAIM" and waiting["phase"] == "100602", (
            waiting
        )

        # A phase whose pull request is open is finished work, not queue work.
        # It is never offered for another run, and the line no longer stops
        # behind it: it is skipped, reported under awaiting_review so the driver
        # can still run the merge check, and the next phase is offered. The old
        # behaviour waited here for a human, which meant an abandoned pull
        # request parked every later phase with no way back.
        review_phase = 100590
        review_file = roadmap / f"phase-{review_phase}-awaiting-review.md"
        review_file.write_text("- [ ] Required approval is obtained.\n")
        (nested / "runs" / f"phase-{review_phase}").mkdir(parents=True, exist_ok=True)
        (nested / "runs" / f"phase-{review_phase}" / "publication.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "phase": f"{review_phase:06d}",
                    "state": "awaiting_review",
                    "branch": "feature/awaiting-review",
                    "pull_request": "https://example/pr/9",
                    "public_head": "0" * 40,
                    "private_head": "0" * 40,
                }
            )
        )
        later_file = roadmap / "phase-100591-after-review.md"
        later_file.write_text("- [ ] Required approval is obtained.\n")
        review = select_server_phase(
            repo, "server-01", store=store_server, roadmap=roadmap
        )
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
        offered = select_server_phase(
            repo, "server-01", store=store_server, reserve=False, roadmap=roadmap
        )
        assert offered["phase"] == "100591", offered
        # The skipped phase rides along on an outcome that does not launch
        # either. That is the case that matters: a tick whose selection is
        # WAIT_FOR_CLAIM is still the tick on which a merge check is due, so
        # dropping the report there would strand the merged phase's receipt.
        drained_record = dict(store_server.read()[0]["phases"]["100591"])
        drained_record["phase"] = "100591"
        (nested / "runs" / "phase-100591").mkdir(exist_ok=True)
        (nested / "runs" / "phase-100591" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        old_runs = conventions.override({"driver.runs": store_server.private / "runs"})
        try:
            store_server.set_status(
                drained_record,
                "completed",
                evidence_path=_self_test_receipt(store_server, drained_record),
            )
        finally:
            conventions.configure(old_runs)
        drained = select_server_phase(
            repo, "server-01", store=store_server, roadmap=roadmap
        )
        assert [e["phase"] for e in (drained.get("awaiting_review") or [])] == [
            f"{review_phase:06d}"
        ], drained
        # And on the one outcome that is not a launch at all.
        drained_state = select_server_phase(
            repo, "server-01", store=store_server, reserve=False, roadmap=roadmap
        )
        assert [e["phase"] for e in (drained_state.get("awaiting_review") or [])] == [
            f"{review_phase:06d}"
        ], drained_state

    with tempfile.TemporaryDirectory() as tmp:
        # The config names the board and run folder. A store that was given a
        # board explicitly keeps that board, while its token uses the configured
        # run folder.
        from argus.board.reservations import _git_test_repo

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
        conventions.override(
            {
                "repo-root": repo,
                "roadmap-dir": BOARD_REL / "roadmap",
                "driver.runs": BOARD_REL / "runs",
                "board.repo": decoy,
            }
        )
        assert conventions.board_repo(repo) == decoy.resolve()
        store = ReservationStore(repo, private_repo=own_private)
        assert store.private == own_private.resolve(), store.private
        got = select_server_phase(
            repo, "server-01", store=store, roadmap=nested / "roadmap"
        )
        assert got["state"] == "RESERVED" and got["phase"] == str(number), got
        assert not (decoy / "runs" / f"phase-{number}" / "reservation.json").exists()
        assert (nested / "runs" / f"phase-{number}" / "reservation.json").exists()
