#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Format private phase-driver notifications as short developer updates."""

from __future__ import annotations

import argparse
import json
import re
import shlex
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
from argus.conventions import phase_run_dir
from argus.notify.labels import display_title, parse_phase_file, phase_label

__all__ = ["display_title", "parse_phase_file", "phase_label"]

_SECRET = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*\S+"
)

_STEP_LABELS = {
    "developer": "Developer",
    "adversary": "Adversary",
    "remediator": "Remediator",
    "approver": "Approver",
    "finalize": "Finalizer",
}


def format_duration(seconds: float) -> str:
    """Format a step duration with the largest useful unit pair."""
    total = int(round(seconds))
    if total < 60:
        text = f"{seconds:.1f}"
        return f"{text} {'second' if text == '1.0' else 'seconds'}"
    if total < 3600:
        minutes, secs = divmod(total, 60)
        text = f"{minutes} {'minute' if minutes == 1 else 'minutes'}"
        if secs:
            text += f" {secs} {'second' if secs == 1 else 'seconds'}"
        return text
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    text = f"{hours} {'hour' if hours == 1 else 'hours'}"
    if minutes:
        text += f" {minutes} {'minute' if minutes == 1 else 'minutes'}"
    return text


def default_run_json(phase_file: str | Path) -> Path:
    """Return the run record the pipeline writes for this phase."""
    number, _, _ = parse_phase_file(phase_file)
    return (
        phase_run_dir(conventions.setting("repo-root"), int(number), phase_file)
        / "run.json"
    )


def stage_timings(run_json: Path) -> list[tuple[str, float]]:
    """Return the per-visit step durations recorded in a run record."""
    try:
        record = cast(
            "dict[str, Any]", json.loads(run_json.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        return []
    timings: list[tuple[str, float]] = []
    for event in record.get("events", []):
        if not isinstance(event, dict):
            continue
        event = cast("dict[str, Any]", event)
        seconds = event.get("duration_s")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            continue
        step = str(event.get("step", "")).strip()
        if not step:
            continue
        visit = event.get("visits")
        visit = visit if isinstance(visit, int) and visit > 0 else 1
        label = _STEP_LABELS.get(step.lower(), step.capitalize())
        timings.append((f"{label} r{visit}", float(seconds)))
    return timings


def completion_details(run_json: Path) -> str | None:
    """Return the step-time breakdown for a completion message, if recorded."""
    timings = stage_timings(run_json)
    if not timings:
        return None
    lines = [f"{name} - {format_duration(seconds)}" for name, seconds in timings]
    total = sum(seconds for _, seconds in timings)
    lines += ["", f"Total: {total / 60:.2f} minutes"]
    return "\n".join(lines)


def _safe_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    first_line = reason.strip().splitlines()[0].strip()
    first_line = _SECRET.sub("<redacted>", first_line)
    return first_line[:180] if first_line else None


def _group_phrase(group: Any) -> str:
    """A trailing clause naming the reservation group, or nothing.

    The group is optional because a phase with no declaration runs alone and its
    messages must read exactly as they always did.
    """
    if not group:
        return ""
    if isinstance(group, str):
        members = [part.strip() for part in group.split(",") if part.strip()]
    else:
        members = [str(part).strip() for part in group if str(part).strip()]
    if not members:
        return ""
    return f" (group: {', '.join(members)})"


def format_notification(
    phase_file: str | Path,
    event: str,
    *,
    reason: str | None = None,
    exit_code: int | None = None,
    quiet_minutes: int = 45,
    run_json: Path | None = None,
    group: Any = None,
    kind: str = "phase",
    claim_label: str | None = None,
) -> str:
    """Format one phase event as a short, user-facing notification."""
    label = phase_label(phase_file, kind=kind)
    # A failed issue run releases its claim; the claim label alone holds it.
    retry = (
        f"Remove the `{claim_label}` label from issue "
        f"#{int(parse_phase_file(phase_file)[0])} to let Argus retry."
        if kind == "issue" and claim_label
        else f"This {kind} is skipped until a person decides."
    )
    safe_reason = _safe_reason(reason)
    suffix = _group_phrase(group) if kind == "phase" else ""
    if event == "started":
        return f"{label}{suffix} has started."
    if event == "released":
        if kind == "issue":
            return f"{label} was released. Another agent may pick it up."
        return (
            f"{label}{suffix} was released. Its group returned to the queue; "
            "another agent may pick it up."
        )
    if event == "completed":
        details = completion_details(run_json or default_run_json(phase_file))
        if details:
            return f"{label}{suffix} has completed.\n\n{details}"
        return f"{label}{suffix} has completed."
    if event == "interrupted":
        detail = f" because {safe_reason}." if safe_reason else "."
        return f"{label}{suffix} was interrupted{detail} The work will resume on the next driver tick."
    if event in {"stopped", "stopped-signal"}:
        if event == "stopped-signal":
            return (
                f"{label} stopped by signal. The driver is halted. "
                "Check the local run logs, then clear the halt when the stop was intentional."
            )
        heading = f"{label} stopped."
        quoted_dir = shlex.quote(str((run_json or default_run_json(phase_file)).parent))
        code = (
            f" The driver is halted with exit code {exit_code}."
            if exit_code is not None
            else " The driver is halted."
        )
        return f"{heading}{code} For more info, run:\n\n```\nls -la {quoted_dir}\n```"
    if event == "escalated":
        # The remedy loop ran out of rounds with findings still open. This is a
        # record about a budget, not a verdict, so the message says the line
        # continues and names the record a person has to read.
        run_dir = (run_json or default_run_json(phase_file)).parent
        return (
            f"{label} exhausted its remedy rounds with findings open. "
            f"The line continues. {retry} "
            f"For the open findings, run:\n\n```\ncat {run_dir / 'remedy-escalated.json'}\n```"
        )
    if event == "failed":
        # The job exhausted its retries; the residue records it and the line
        # continues. The notice names the record a person has to read.
        run_dir = (run_json or default_run_json(phase_file)).parent
        code = f" with exit code {exit_code}" if exit_code is not None else ""
        return (
            f"{label} failed after its retries{code}. "
            f"The line continues. {retry} "
            f"For the residue, run:\n\n```\ncat {run_dir / 'failed.json'}\n```"
        )
    if event == "stalled":
        quiet = "under a minute" if quiet_minutes < 1 else f"{quiet_minutes} minutes"
        return (
            f"{label} looks stalled.\n\n"
            f"There has been no new output for {quiet}. "
            "The session is still running; inspect it before stopping the work."
        )
    raise ValueError(f"unknown phase event: {event}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-file", required=True)
    parser.add_argument("--kind", choices=("phase", "issue"), default="phase")
    parser.add_argument(
        "--event",
        required=True,
        choices=[
            "started",
            "completed",
            "interrupted",
            "released",
            "stopped",
            "stopped-signal",
            "stalled",
            "escalated",
            "failed",
        ],
    )
    parser.add_argument("--reason")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--quiet-minutes", type=int)
    parser.add_argument(
        "--group",
        action="append",
        default=None,
        help="a phase in the reservation group, repeatable; adds a group clause",
    )
    parser.add_argument("--config")
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--phases-directory", dest="config_phases_directory")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conventions.configure_from_args(args)
    except ConfigError as exc:
        print(f"phase notifications: {exc}", file=sys.stderr)
        return 2
    print(
        format_notification(
            args.phase_file,
            args.event,
            reason=args.reason,
            exit_code=args.exit_code,
            quiet_minutes=(
                args.quiet_minutes
                if args.quiet_minutes is not None
                else conventions.setting("driver.quiet-minutes")
            ),
            group=args.group,
            kind=args.kind,
            claim_label=(
                conventions.setting("issues.claim-label")
                if args.kind == "issue"
                else None
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
