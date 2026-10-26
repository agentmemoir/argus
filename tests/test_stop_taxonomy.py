# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stop taxonomy for the packages the driver and gitsync tripwire does not scan.

The driver and gitsync shell and Python stops have their own tripwire
(``test_stop_tripwire``). This file covers the remaining runtime packages:
``board``, ``config``, ``issues``, and ``select``. Every stop row is keyed by
file, rule, and the normalized stop line, never by a line number, and every
rule carries one reason kind. The reason kinds are the owner's allowed reasons:
``reservation``, ``running-job``, ``own-state``, and the separate start-time
``start-time``; ``operator``, ``advisory``, and ``repair`` mark the remaining
report-only forms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest

import test_stop_tripwire
from stop_taxonomy_data import ALLOWLIST_ROWS

ROOT = Path(__file__).resolve().parents[1] / "src/argus"
PACKAGES = ("board", "config", "issues", "select")

PY_RULES = {
    "raise_fail": re.compile(r"raise\s+Fail\("),
    "raise_config": re.compile(r"raise\s+(ConfigError|ConfigException)\("),
    "raise_coordination": re.compile(
        r"raise\s+(CoordinationError|CoordinationUnavailable)\("
    ),
    "raise_fenced": re.compile(r"raise\s+ReservationFenced\("),
    "raise_busy": re.compile(r"raise\s+PublicationBusy\("),
    "raise_value": re.compile(r"raise\s+ValueError\("),
    "blocked_call": re.compile(r"(?<![\w.])blocked\("),
    "ok_false": re.compile(r"\bok[\"']?\s*[:=]\s*False|ok=False"),
}

#: The reason kind per rule, across every package in the taxonomy.
RULE_KINDS: dict[str, str] = {
    # driver and gitsync (the other tripwire).
    "exit_nonzero": "operator",
    "return_nonzero": "own-state",
    "drive_rc_nonzero": "own-state",
    "drive_rc_zero_refusal": "repair",
    "halt_marker": "own-state",
    "resume_refuse": "reservation",
    "raise_fail": "own-state",
    "raise_config": "start-time",
    "raise_coordination": "reservation",
    "raise_fenced": "reservation",
    "raise_busy": "reservation",
    "raise_value": "start-time",
    "blocked_call": "own-state",
    "ok_false": "repair",
}

REASON_KINDS: dict[str, str] = {
    "operator": "an operator command, usage, or manual stop",
    "own-state": "argus's own state or a narrow lost-work stop",
    "reservation": "argus's own reservation, running job, or board lock",
    "start-time": "start-time configuration or usage; names the key and fix",
    "repair": "a report-only retry or refusal the caller continues",
}


def _allowed() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for line in ALLOWLIST_ROWS.strip().splitlines():
        rel, rule, anchor = line.strip().split("\t", 2)
        out.add((rel, rule, anchor))
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())[:200]


def _scan(
    sources: Iterable[tuple[str, Path]] | None = None,
) -> list[tuple[str, str, str]]:
    if sources is None:
        sources = [(package, ROOT / package) for package in PACKAGES]
    findings: list[tuple[str, str, str]] = []
    for name, directory in sources:
        for path in sorted(directory.glob("*.py")):
            if "selftest" in path.name:
                continue
            rel = f"{name}/{path.name}"
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("#"):
                    continue
                for rule, pattern in PY_RULES.items():
                    if pattern.search(line):
                        findings.append((rel, rule, _normalize(line)))
    return findings


def test_no_unowned_stop_in_the_taxonomy() -> None:
    allowed = _allowed()
    unexpected = [f for f in _scan() if f not in allowed]
    assert not unexpected, (
        "a stop path must be removed or allowlisted with an owner reason in "
        f"stop_taxonomy_data.py: {unexpected}"
    )


def test_every_rule_has_an_owner_reason_kind() -> None:
    for rule, kind in RULE_KINDS.items():
        assert kind in REASON_KINDS, rule
    for rule in test_stop_tripwire.REASONS:
        assert rule in RULE_KINDS, f"the tripwire rule {rule!r} has no reason kind"
    for _rel, rule, _anchor in _allowed():
        assert rule in RULE_KINDS, f"allowlisted rule {rule!r} has no reason kind"


def test_every_stop_form_has_a_row() -> None:
    allowed = _allowed()
    rules_with_rows = {rule for _rel, rule, _anchor in allowed}
    findings = _scan()
    for rule in {rule for _rel, rule, _anchor in findings}:
        assert rule in rules_with_rows, f"stop form {rule!r} has no allowlist row"


def test_an_injected_taxonomy_stop_fails(tmp_path: Path) -> None:
    package = tmp_path / "board"
    package.mkdir()
    (package / "new_store.py").write_text("def f():\n    raise ValueError('x')\n")
    injected = [f for f in _scan(sources=[("board", package)]) if f not in _allowed()]
    assert ("board/new_store.py", "raise_value", "raise ValueError('x')") in injected


@pytest.mark.parametrize("rule", sorted(PY_RULES))
def test_rule_kinds_cover_the_scanner(rule: str) -> None:
    assert rule in RULE_KINDS
