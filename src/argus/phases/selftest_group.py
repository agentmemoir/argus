# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-test cases for a phase's reservation-group declaration."""

import json
import tempfile
from pathlib import Path

from argus import conventions
from argus.phases.group import ReservationGroupError, resolve_group


def _declaration_checks() -> None:
    """A symmetric declaration resolves; every other shape is refused by name."""
    with tempfile.TemporaryDirectory() as tmp:
        # A reservation group is declared in a fenced ``### Reservation Group``
        # section, read with the same section shape as a decision gate, and is
        # symmetric: every member names the same set. Resolution is read-only;
        # it names the group and the members still to run but claims nothing.
        # These assertions cover the group a phase resolves to and the
        # documents that are refused.
        repo = Path(tmp).resolve()
        phases = repo / "phases"
        phases.mkdir()
        old = conventions.override(
            {
                "driver.include": ["phase-*.md"],
                "driver.exclude": [],
                "driver.runs": repo / "runs",
            }
        )
        try:

            def write_phase(name: str, together: str | None = None) -> None:
                text = "## 1. Objective\n"
                if together is not None:
                    text += "### Reservation Group\n\n"
                    text += f"`together: {together}`\n"
                (phases / name).write_text(text)

            # A symmetric three-member group with one member finished: the
            # remainder is the other two, in declaration order.
            for name in ("phase-100410-a.md", "phase-100420-b.md", "phase-100430-c.md"):
                write_phase(name, "100410, 100420, 100430")
            (repo / "runs" / "phase-100410").mkdir(parents=True)
            (repo / "runs" / "phase-100410" / "run.json").write_text(
                json.dumps({"state": "completed"})
            )
            got = resolve_group(repo, phases, 100420)
            assert got["state"] == "GROUP", got
            assert got["group"] == ["100410", "100420", "100430"], got
            assert got["remainder"] == ["100420", "100430"], got
            from_other = resolve_group(repo, phases, 100410)
            assert from_other["group"] == got["group"], from_other
            assert from_other["remainder"] == ["100420", "100430"], from_other

            # Every member finished means nothing is left to run.
            for number in (100420, 100430):
                rd = repo / "runs" / f"phase-{number}"
                rd.mkdir(parents=True, exist_ok=True)
                (rd / "run.json").write_text(json.dumps({"state": "completed"}))
            assert resolve_group(repo, phases, 100430)["remainder"] == [], (
                resolve_group(repo, phases, 100430)
            )

            # A phase with no group section is refused, and the message says
            # which section is missing.
            write_phase("phase-100440-d.md")
            try:
                resolve_group(repo, phases, 100440)
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
                resolve_group(repo, phases, 100450)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100460" in str(exc), exc
            assert refused, "an asymmetric group must be refused"

            # A member named in a declaration that has no document.
            write_phase("phase-100480-h.md", "100480, 100490")
            try:
                resolve_group(repo, phases, 100480)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100490" in str(exc), exc
            assert refused, "a group member with no document must be refused"

            # A phase whose own declaration does not list it cannot resolve.
            write_phase("phase-100500-i.md", "100510")
            write_phase("phase-100510-l.md", "100510")
            try:
                resolve_group(repo, phases, 100500)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "100500" in str(exc), exc
            assert refused, "a phase not listed in its own group must be refused"

            # A token that is not a six-digit phase number.
            write_phase("phase-100520-j.md", "12345")
            try:
                resolve_group(repo, phases, 100520)
                refused = False
            except ReservationGroupError as exc:
                refused = True
                assert "12345" in str(exc), exc
            assert refused, "an invalid phase token must be refused"

            # A group of one is legal; it is unfinished until done.
            write_phase("phase-100530-k.md", "100530")
            solo = resolve_group(repo, phases, 100530)
            assert solo["group"] == ["100530"], solo
            assert solo["remainder"] == ["100530"], solo
        finally:
            conventions.configure(old)


__all__ = ["_declaration_checks"]
