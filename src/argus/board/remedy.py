#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Close out a phase whose remedy approver ran out of rounds.

Exhausting the approver's round budget is a fact about the budget, not a verdict
on the work. The pipeline records that as the non-terminal state
``remedy_escalated``. This module does the host-side work the state cannot do by
itself: it writes the open findings and where their grounds live to a durable
record in the run directory, and it releases the phase reservation so no other
machine is blocked by a claim nobody is working on.

It deliberately does not file a GitHub issue. The escalation is pipeline
internal, so the issue would have to go to the private repository, and there is
no private issue helper the driver may call; writing a public issue would leak
private run state. The record written here is the durable copy, and the driver's
escalation notification points at it.

A failure here never halts the line. The run record's state is what the selector
reads, so an escalation that cannot write its record or release its claim is
still skipped, and the driver logs the failure.

Usage:
    python3 argus/remedy_escalation.py --run-dir <dir> [--json]
    python3 argus/remedy_escalation.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.config.loader import ConfigError

#: The name of the durable record, mirroring ``publication.json``.
RECORD_NAME = "remedy-escalated.json"

#: A finding id, as the approver writes it in a verdict signal.
_FINDING_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")

#: The step whose last signal names the open findings.
APPROVER_STEP = "approver"

#: How a person gets back into an escalated phase. Recorded so the state is not
#: a dead end: tick the phase file's approval box to close it out, or start the
#: run over explicitly (glide --fresh archives the run directory).
RESUME = (
    "A person decides: tick the phase file's `Required approval is obtained` "
    "box to close the phase out, or re-run it explicitly with `--fresh` to "
    "archive this run and start over."
)


def last_approver_signal(run_json: Path) -> str | None:
    """The last approver signal recorded in a run record, or ``None``.

    Read from ``events`` because that is the per-invocation record: the last
    approver visit is the one that exhausted the budget. An unreadable or
    shapeless record is not an error here; the record written without it is
    still useful.
    """
    try:
        data = json.loads(run_json.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data = cast("dict[str, Any]", data)
    signal: str | None = None
    events: list[Any] = data.get("events") or []
    for event in events:
        event_obj = cast("dict[str, Any]", event)
        if isinstance(event, dict) and event_obj.get("step") == APPROVER_STEP:
            value = event_obj.get("signal")
            if isinstance(value, str) and value:
                signal = value
    return signal


def approver_logs(phase_dir: Path) -> list[str]:
    """The approver round logs beside the run record, oldest first."""
    try:
        return [p.name for p in sorted(phase_dir.glob("approver-task-r*.log"))]
    except OSError:
        return []


def _open_findings(signal: str | None) -> list[str]:
    """The distinct finding ids named in a verdict signal, in first-seen order."""
    if not signal:
        return []
    found: list[str] = []
    for match in _FINDING_RE.findall(signal):
        if match not in found:
            found.append(match)
    return found


def record_escalation(
    phase_dir: Path, phase: str | None = None, *, issues: bool = False
) -> dict[str, Any]:
    """Write the durable escalation record and return it.

    Args:
        phase_dir: The phase's run directory, which must exist.
        phase: The phase key, when the caller already knows it.

    Returns:
        The record that was written.
    """
    phase_dir = Path(phase_dir)
    run_json = phase_dir / "run.json"
    signal = last_approver_signal(run_json)
    kind = "issue" if issues else "phase"
    key = phase or phase_dir.name.removeprefix(f"{kind}-")
    record: dict[str, Any] = {
        "version": 1,
        kind: int(key)
        if issues
        else f"{int(key):06d}"
        if str(key).isdigit()
        else str(key),
        "state": "remedy_escalated",
        "via": "exhausted",
        "approver_signal": signal,
        "open_findings": _open_findings(signal),
        "approver_logs": approver_logs(phase_dir),
        "run_dir": str(phase_dir),
        "record": str(phase_dir / RECORD_NAME),
        "resume": "Clear the issue board escalation receipt before retrying."
        if issues
        else RESUME,
    }
    (phase_dir / RECORD_NAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    return record


def release_claim(
    repo_root: Path, reservation: dict[str, Any], *, issues: bool = False
) -> Any:
    """Mark the phase's board record rejected, so the claim is not retained.

    Returns the store's result. Raises whatever the store raises; the caller
    treats a failure as reportable but not fatal, because the selector reads the
    run record's state and skips the phase whether or not the board agreed.
    """
    from argus.board.reservations import ReservationStore

    store = ReservationStore(Path(repo_root), issues=issues)
    if issues:
        store.set_issue_receipt(
            reservation, {"state_reason": "remedy_escalated", "cleared": False}
        )
        return store.release_group(reservation)
    return store.set_status(reservation, "rejected")


def _reservation_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    if not (args.machine_id and args.reservation_id and args.generation is not None):
        return None
    return {
        "issue" if args.issues else "phase": int(args.phase)
        if args.issues
        else str(args.phase),
        "machine_id": args.machine_id,
        "reservation_id": args.reservation_id,
        "generation": int(args.generation),
    }


def run(args: argparse.Namespace) -> int:
    phase_dir = Path(args.run_dir)
    if not phase_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"no run directory: {phase_dir}"}))
        return 1
    record = record_escalation(phase_dir, args.phase, issues=args.issues)
    released = "skipped"
    reason = ""
    reservation = _reservation_from_args(args)
    if reservation is not None:
        try:
            release_claim(Path(args.repo_root), reservation, issues=args.issues)
            released = "released" if args.issues else "rejected"
        except Exception as exc:  # noqa: BLE001 - a release failure is reported
            released = "failed"
            reason = (
                str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__
            )
    result: dict[str, Any] = {
        "ok": True,
        "record": record["record"],
        "released": released,
        "release_error": reason,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", help="the phase run directory")
    parser.add_argument("--phase", default=None)
    parser.add_argument("--issues", action="store_true")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--config")
    parser.add_argument("--phases-dir", dest="config_phases_dir")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--machine-id", default=None)
    parser.add_argument("--reservation-id", default=None)
    parser.add_argument("--generation", type=int, default=None)
    parser.add_argument("--self-test", action="store_true")
    return parser


def self_test() -> int:
    import tempfile

    checks = 0
    with tempfile.TemporaryDirectory() as tmp:
        phase_dir = Path(tmp) / "phase-100901"
        phase_dir.mkdir()
        (phase_dir / "run.json").write_text(
            json.dumps(
                {
                    "state": "remedy_escalated",
                    "events": [
                        {
                            "step": "approver",
                            "signal": "REMEDY_REJECTED: F-01 aaa11111",
                        },
                        {"step": "remediator", "signal": "REMEDIATOR_DONE bbb22222"},
                        {
                            "step": "approver",
                            "signal": "REMEDY_REJECTED: NEW-04 c0eb2c73",
                        },
                    ],
                }
            )
            + "\n"
        )
        (phase_dir / "approver-task-r3.log").write_text(
            "REMEDY_REJECTED: NEW-04 c0eb2c73\n"
        )
        record = record_escalation(phase_dir, "100901")
        assert record["state"] == "remedy_escalated", record
        assert record["via"] == "exhausted", record
        assert record["open_findings"] == ["NEW-04"], record
        assert record["approver_logs"] == ["approver-task-r3.log"], record
        assert (phase_dir / RECORD_NAME).is_file(), "the record was not written"
        checks += 1

        # A missing run record still yields a usable record, so a corrupt run
        # directory does not stop the escalation from being written down.
        bare = Path(tmp) / "phase-100902"
        bare.mkdir()
        record = record_escalation(bare, "100902")
        assert record["open_findings"] == [] and record["approver_signal"] is None, (
            record
        )
        assert (bare / RECORD_NAME).is_file()
        checks += 1
    print(f"remedy_escalation self-test: pass ({checks} checks)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        conventions.configure_from_args(args)
    except ConfigError as exc:
        print(f"remedy_escalation: {exc}", file=sys.stderr)
        return 2
    args.repo_root = str(conventions.setting("repo-root"))
    if not args.run_dir:
        print("remedy_escalation: --run-dir is required", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
