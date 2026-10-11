#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared phase policy for the private roadmap pipeline.

Two independent rules live here:

* Phase *numbers* at or above ``PARKED_PHASE_FLOOR`` are roadmap-only artifacts.
  They stay visible in the roadmap and index for planning, but the pipeline
  selector never picks them and the runner refuses to launch them.

* Phase *decision gates* are declared in each plan's ``### Decision Gates``
  section and carry a state. An ``open`` gate means the owner owes a decision,
  and the phase is not runnable. This is defined in ``roadmap/decision-gates.md``.

The gate check is read-only. It reports; it does not gate selection, because
wiring it into the selector changes which phases run and needs its own decision.
"""

import argparse
import pathlib
import re
import sys
from collections.abc import Iterator

if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.config.loader import ConfigError
from argus.conventions import phase_file_parts, phase_paths

PARKED_PHASE_FLOOR = 900000

GATES_HEADING = "### Decision Gates"
HEADING_RE = re.compile(r"^#{2,3} ")
GATE_STATES = ("none", "recorded", "open")

#: A gate row is runnable only when the owner has actually made the choice.
RUNNABLE_GATE_STATES = ("none", "recorded")


def is_runnable_phase(number: int) -> bool:
    """True when ``number`` may be selected or launched by the pipeline."""
    return int(number) < PARKED_PHASE_FLOOR


def _section_body(text: str) -> str | None:
    """Return the body of the ``### Decision Gates`` section, or ``None``.

    The section ends at the next ``##``/``###`` heading or at a ``---`` rule,
    whichever comes first, so a table cannot bleed into the following section.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == GATES_HEADING:
            start = i + 1
            break
    if start is None:
        return None

    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if HEADING_RE.match(line) or stripped == "---":
            break
        body.append(line)
    return "\n".join(body)


def _gate_rows(body: str) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(gate, issue, state, choice)`` for each data row of the table."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        gate, issue, state, choice = cells[0], cells[1], cells[2], cells[3]
        # Skip the header row and the |---|---| separator.
        if not state or set(state) <= set("-: "):
            continue
        if gate.lower() == "gate":
            continue
        # The state cell is a backticked word, optionally followed by the date
        # it was recorded, so take the first word and ignore the rest.
        words = state.replace("`", " ").split()
        state = words[0].lower() if words else ""
        yield gate, issue, state, choice.strip("` ").strip()


def phase_gate_state(path: str | pathlib.Path) -> tuple[str, str]:
    """Return ``(state, detail)`` for one phase plan file.

    ``state`` is one of ``none``, ``recorded``, ``open``, or ``unknown``.
    ``unknown`` means the plan predates the gate policy and carries no
    ``### Decision Gates`` section at all. That is deliberately distinct from
    ``none``: a missing section and an empty one are different facts, and
    collapsing them is what let 100815 reach a run unblocked.
    """
    body = _section_body(pathlib.Path(path).read_text(encoding="utf-8"))
    if body is None:
        return "unknown", "no '### Decision Gates' section"

    rows = list(_gate_rows(body))
    if not rows:
        if re.search(r"^\s*none\b", body, re.IGNORECASE | re.MULTILINE):
            return "none", "section declares no product decision"
        return "unknown", "section has no gate table and does not declare 'none'"

    # A table exists, so the phase has gates. If none of them blocks, the
    # aggregate state is `recorded`, not `none`: `none` means the phase
    # declared it has no product decision at all, which is a different fact.
    worst = "recorded"
    detail = f"{len(rows)} gate(s), all recorded"
    for gate, _issue, state, choice in rows:
        if state not in GATE_STATES:
            worst, detail = "open", f"{gate}: unrecognised state {state!r}"
            break
        if state == "recorded" and choice in ("", "-", "--", "—"):
            # Fail closed: the state word is a claim, the choice is the fact.
            worst, detail = "open", f"{gate}: state 'recorded' but no choice given"
            break
        if state == "open":
            worst, detail = "open", f"{gate}: owner decision outstanding"
            break
    return worst, detail


def phase_gates_blocked(path: str | pathlib.Path) -> bool:
    """True when the plan carries a gate that must stop the phase being run."""
    state, _ = phase_gate_state(path)
    return state == "open"


def scan_gates(roadmap_dir: str | pathlib.Path) -> dict[str, tuple[str, str, str]]:
    """Return ``{phase_number: (state, detail, filename)}`` for every plan."""
    found: dict[str, tuple[str, str, str]] = {}
    for entry in phase_paths(roadmap_dir):
        parts = phase_file_parts(entry)
        if parts is None or not entry.is_file():
            continue
        state, detail = phase_gate_state(entry)
        found[str(parts[0])] = (state, detail, entry.name)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report phase decision gates that block a run."
    )
    parser.add_argument(
        "--open-gates",
        action="store_true",
        help="list phases whose decision gates are not runnable",
    )
    parser.add_argument(
        "--roadmap",
        "--roadmap-dir",
        dest="config_roadmap_dir",
        default=None,
        help="roadmap directory for this run",
    )
    parser.add_argument("--config")
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument(
        "--all", action="store_true", help="list every phase, not just blockers"
    )
    args = parser.parse_args(argv)

    if not args.open_gates and not args.all:
        parser.error("choose --open-gates or --all")

    try:
        conventions.configure_from_args(args)
    except ConfigError as exc:
        print(f"phase policy: {exc}", file=sys.stderr)
        return 2

    roadmap = pathlib.Path(conventions.setting("roadmap-dir"))
    gates = scan_gates(roadmap)
    blocked = {n: v for n, v in gates.items() if v[0] == "open"}
    unknown = {n: v for n, v in gates.items() if v[0] == "unknown"}
    shown = gates if args.all else blocked

    for number in sorted(shown):
        state, detail, name = shown[number]
        print(f"{number}  {state:<9} {name}\n{'':>11}{detail}")

    print(
        f"\n{len(gates)} phases: {len(blocked)} blocked by an open gate, "
        f"{len(unknown)} with no gate section (unchecked).",
        file=sys.stderr,
    )
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
