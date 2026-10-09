# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test cases for completion evidence and the roadmap folder."""

import json
import tempfile
from pathlib import Path

from argus import conventions
from argus.conventions import runs_dir
from argus.roadmap.evidence import (
    RoadmapUnavailable,
    is_done,
    ledger_finalized,
    phase_attempted,
    reported_path,
    run_completed,
    signals,
)
from argus.roadmap.queue import candidates, select_next
from argus.roadmap.selftest_fixture import BOARD_REL
from argus.roadmap.server import RoadmapSource


def _evidence_checks():
    """Each completion marker alone, and the folder a machine was told to read."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "runs" / "phase-100030").mkdir(parents=True)
        (repo / "runs" / "phase-100040").mkdir(parents=True)
        (repo / "roadmap" / "phase-100010-alpha.md").write_text(
            "- [x] Required approval is obtained.\n"
        )
        (repo / "roadmap" / "phase-100020-appendix-notes.md").write_text("x\n")
        (repo / "roadmap" / "phase-100020-beta.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        (repo / "roadmap" / "phase-100030-gamma.md").write_text("x\n")
        (repo / "runs" / "phase-100030" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        # 100040: only index.md marks it Complete (template drift case).
        (repo / "roadmap" / "phase-100040-delta.md").write_text("x\n")
        (repo / "runs" / "phase-100040" / "run.json").write_text(
            json.dumps({"state": "rejected"})
        )
        (repo / "roadmap" / "index.md").write_text(
            "| 100040 | `1×` | [x](phase-100040-delta.md) | Complete — PASS |\n"
            "| 100020 | `1×` | [x](phase-100020-beta.md) | Plan ready |\n"
            "## 100010. alpha · `1×` · **Complete** · [x](phase-100010-alpha.md)\n"
        )
        got = select_next(repo, repo / "roadmap")
        assert got is not None and got[0] == 100020, got
        assert got[1].name == "phase-100020-beta.md", got
        # Closed out by hand, the way an operator retires a phase the pipeline
        # rejected. The run record is what makes the tick count; without it this
        # phase is one nothing ever ran and the tick is ignored.
        (repo / "runs" / "phase-100020").mkdir(parents=True)
        (repo / "runs" / "phase-100020" / "run.json").write_text(
            json.dumps({"state": "rejected"})
        )
        (repo / "roadmap" / "phase-100020-beta.md").write_text(
            "- [x] Required approval is obtained.\n"
        )
        assert select_next(repo, repo / "roadmap") is None, select_next(
            repo, repo / "roadmap"
        )
    with tempfile.TemporaryDirectory() as tmp:
        # Parked phases stay visible in the roadmap but are never selected.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "phase-100010-alpha.md").write_text(
            "- [x] Required approval is obtained.\n"
        )
        (repo / "roadmap" / "phase-900999-parked.md").write_text("x\n")
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        parked_rd = repo / "runs" / "phase-100010"
        parked_rd.mkdir(parents=True)
        (parked_rd / "run.json").write_text(json.dumps({"state": "rejected"}))
        assert select_next(repo, repo / "roadmap") is None, select_next(
            repo, repo / "roadmap"
        )
        assert [n for n, _ in candidates(repo, repo / "roadmap")] == [100010], (
            candidates(repo, repo / "roadmap")
        )
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
        assert not phase_attempted(repo, 100010), (
            "a phase with no run directory has not been attempted"
        )
        assert not is_done(repo, 100010, phase, set()), (
            "a ticked approval box must not finish a phase nothing ever ran"
        )
        assert select_next(repo, repo / "roadmap") is not None, (
            "a phase with only a ticked approval box is still offered"
        )
        # The box is still reported, so the audit output shows why a human
        # believes the phase is finished.
        assert signals(repo, 100010, phase, set())["approval"] is True, signals(
            repo, 100010, phase, set()
        )
        # Once the pipeline has run the phase, the same box closes it out. A
        # rejected run is not completion on its own, so this is the tick doing
        # the work, which is the operator close-out this rule exists to keep.
        rd = repo / "runs" / "phase-100010"
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({"state": "rejected"}))
        assert phase_attempted(repo, 100010), (
            "a run directory is proof the phase was reached"
        )
        assert not run_completed(repo, 100010), "a rejected run is not completion"
        assert is_done(repo, 100010, phase, set()), (
            "a ticked box closes out a phase the pipeline already ran"
        )
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
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONE"}}})
        )
        # The runner writes the digest form, not the bare word. This case is
        # the one that regressed: with an exact `== "FINALIZE_DONE"` compare,
        # the real shape never matched, so every finished phase read as
        # unfinished. Assert it directly so it cannot come back silently, on
        # numbers this block does not otherwise use, so `select_next` below is
        # unaffected.
        rd_digest = repo / "runs" / "phase-100070"
        rd_digest.mkdir(parents=True)
        (rd_digest / "ledger.json").write_text(
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONE 3d0dbe62"}}})
        )
        assert ledger_finalized(repo, 100070), "a digested FINALIZE_DONE must count"
        # A near miss must not: same prefix, different word.
        rd_near = repo / "runs" / "phase-100080"
        rd_near.mkdir(parents=True)
        (rd_near / "ledger.json").write_text(
            json.dumps({"steps": {"finalize": {"signal": "FINALIZE_DONEA 3d0dbe62"}}})
        )
        assert not ledger_finalized(repo, 100080), "FINALIZE_DONEA is not FINALIZE_DONE"
        (repo / "roadmap" / "phase-100020-beta.md").write_text(unchecked)
        rd2 = repo / "runs" / "phase-100020"
        rd2.mkdir(parents=True)
        (rd2 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out complete\nFINALIZE_DONE r1nonce\n"
        )
        (repo / "roadmap" / "phase-100030-gamma.md").write_text(unchecked)
        rd3 = repo / "runs" / "phase-100030"
        rd3.mkdir(parents=True)
        (rd3 / "ledger.json").write_text(
            json.dumps({"steps": {"developer": {"signal": "DEVELOPER_DONE"}}})
        )
        (rd3 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out complete\nFINALIZE_DONE\n"
        )
        (repo / "roadmap" / "phase-100040-delta.md").write_text(unchecked)
        rd4 = repo / "runs" / "phase-100040"
        rd4.mkdir(parents=True)
        (rd4 / "finalize-task-r1.log").write_text(
            "2026-01-01T00:00:00Z close-out blocked\nFINALIZE_BLOCKED: trouble\n"
        )
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
            "- [ ] Required approval is obtained.\n"
        )
        (priv_map / "index.md").write_text("Plan ready\n")
        conventions.override(
            {
                "roadmap-dir": BOARD_REL / "roadmap",
                "driver.runs": BOARD_REL / "runs",
            }
        )
        assert RoadmapSource(repo).roadmap == priv_map
        assert runs_dir(repo) == priv_wf.parent, runs_dir(repo)
        got = select_next(repo, priv_map)
        assert got is not None and got[0] == 100010, got
        assert got[1].relative_to(repo) == (
            BOARD_REL / "roadmap" / "phase-100010-alpha.md"
        ), got
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
                "- [ ] Required approval is obtained.\n"
            )
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
        assert reported_path(repo, got[1]) == str(
            outside / "phase-100460-external.md"
        ), reported_path(repo, got[1])
        # Inside the repository it is still reported relative, unchanged.
        assert reported_path(repo, named / "phase-100020-named.md") == str(
            BOARD_REL / "elsewhere" / "phase-100020-named.md"
        ), reported_path(repo, named / "phase-100020-named.md")
        # The file's value selects the roadmap when no CLI override is passed.
        assert RoadmapSource(repo).roadmap == repo / BOARD_REL / "roadmap"
