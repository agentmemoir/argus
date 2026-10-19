# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Tripwire: a new non-owned stop in the driver or gitsync fails this test.

The allowlist is keyed by file, rule, and the normalized stop line, never by a
line number, and every rule carries one owner reason. A stop that sits behind a
helper is attributed to the helper's callers. Add a stop and this test fails
until an owner reason is recorded here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stop_allowlist import STOP_ALLOWLIST as _ALLOWLIST

ROOT = Path(__file__).resolve().parents[1] / "src/argus"
DRIVER = ROOT / "driver"
GITSYNC = ROOT / "gitsync"

SHELL_RULES = {
    "exit_nonzero": re.compile(r"(?<![\w-])exit\s+([1-9][0-9]*)\b"),
    "return_nonzero": re.compile(r"(?<![\w-])return\s+([1-9][0-9]*)\b"),
    "drive_rc_nonzero": re.compile(r"_DRIVE_RC=([1-9][0-9]*)\b"),
    "drive_rc_zero_refusal": re.compile(r"_DRIVE_RC=0\b"),
    "halt_marker": re.compile(r">\s*\"\$HALT\""),
    "resume_refuse": re.compile(r"(?<![\w-])resume_refuse\b"),
}

PY_RULES = {
    "raise_fail": re.compile(r"raise\s+Fail\("),
    "raise_config": re.compile(r"raise\s+(ConfigError|ConfigException)\("),
    "raise_coordination": re.compile(
        r"raise\s+(CoordinationError|CoordinationUnavailable)\("
    ),
    "raise_fenced": re.compile(r"raise\s+ReservationFenced\("),
    "raise_busy": re.compile(r"raise\s+PublicationBusy\("),
    "blocked_call": re.compile(r"(?<![\w.])blocked\("),
    "ok_false": re.compile(r"\bok[\"']?\s*[:=]\s*False|ok=False"),
}

REFUSAL_WORDS = re.compile(
    r"refus|no launch|not runnable|halt|no new work|stop|block", re.IGNORECASE
)
HELPER_STOPS = {"resume_refuse", "driver_orphan_check", "on_stop"}

REASONS = {
    "exit_nonzero": "operator command, usage, broken install, or manual resume; not a tick stop",
    "return_nonzero": "a helper signal, an owned check, or the narrow lost-work stop at an unpreservable tidy",
    "drive_rc_nonzero": "an owned check, or the narrow lost-work stop at an unpreservable tidy",
    "drive_rc_zero_refusal": "a no-launch tick that warns and continues",
    "halt_marker": "operator stop, signal to a running job, orphaned run, or configuration halt",
    "resume_refuse": "owned reservation, running job, own state, or a usage error on --resume",
    "raise_fail": "glide or an owned run-state check; the caller repairs or retries",
    "raise_config": "start-time configuration or usage error; names the key and the fix",
    "raise_coordination": "owned reservation board; retries next tick",
    "raise_fenced": "owned reservation fence; retries next tick",
    "raise_busy": "owned publication lock; retries next tick",
    "blocked_call": "narrow lost-work stop, start-time configuration, or a publication retry",
    "ok_false": "retry or refusal signal for a caller that reports and continues",
}


def _allowed() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for line in _ALLOWLIST.strip().splitlines():
        rel, rule, anchor = line.strip().split("\t", 2)
        out.add((rel, rule, anchor))
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())[:200]


def _spans(text: str) -> list[tuple[str, int, int]]:
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", line)
        if match:
            starts.append((match.group(1), i))
    out: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else 10**9
        out.append((name, start, end))
    return out


def _py_spans(text: str) -> list[tuple[str, int, int]]:
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            starts.append((match.group(1), i))
    out: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else 10**9
        out.append((name, start, end))
    return out


def _owner(spans: list[tuple[str, int, int]], line_number: int) -> str:
    for name, start, end in spans:
        if start <= line_number < end:
            return name
    return "<top>"


def _callers(texts: dict[str, str], name: str) -> list[str]:
    found: list[str] = []
    for _rel, text in texts.items():
        for other, start, end in _spans(text):
            if other == name:
                continue
            body = "\n".join(text.splitlines()[start:end])
            if re.search(rf"(?<![\w-]){re.escape(name)}\b", body):
                found.append(other)
    return sorted(set(found))


def _scan(
    driver: Path = DRIVER, gitsync: Path = GITSYNC
) -> list[tuple[str, str, str, str, list[str]]]:
    findings: list[tuple[str, str, str, str, list[str]]] = []
    shell_texts = {
        f"driver/{path.name}": path.read_text()
        for path in sorted(driver.glob("*.sh"))
        if "selftest" not in path.name and path.name != "selftest-support.sh"
    }
    for rel, text in shell_texts.items():
        spans = _spans(text)
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#"):
                continue
            owner = _owner(spans, i)
            for rule, pattern in SHELL_RULES.items():
                if not pattern.search(line):
                    continue
                if rule == "drive_rc_zero_refusal":
                    window = " ".join(text.splitlines()[max(0, i - 2) : i + 1])
                    if not REFUSAL_WORDS.search(window):
                        continue
                # The owner function is part of the key: a new stop in a new
                # function is not covered by an allowlist row for the same line
                # text in another function.
                anchor = f"fn:{owner}|{_normalize(line)}"
                helper = owner if owner in HELPER_STOPS else ""
                callers = _callers(shell_texts, owner) if helper else []
                findings.append((rel, rule, anchor, helper, callers))
    py_texts = {
        f"gitsync/{path.name}": path.read_text()
        for path in sorted(gitsync.glob("*.py"))
        if "selftest" not in path.name
    }
    for rel, text in py_texts.items():
        spans = _py_spans(text)
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#"):
                continue
            owner = _owner(spans, i)
            for rule, pattern in PY_RULES.items():
                if pattern.search(line):
                    findings.append(
                        (rel, rule, f"fn:{owner}|{_normalize(line)}", "", [])
                    )
    return findings


def test_no_unowned_stop_appears() -> None:
    allowed = _allowed()
    unexpected = [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan()
        if (rel, rule, anchor) not in allowed
    ]
    assert not unexpected, (
        "a new stop path must be removed or allowlisted with an owner reason in "
        f"{__file__}: {unexpected}"
    )


def test_every_allowed_rule_has_an_owner_reason() -> None:
    for _rel, rule, _anchor in _allowed():
        assert rule in REASONS, f"allowlisted rule {rule!r} has no owner reason"


def test_a_helper_stop_names_its_callers() -> None:
    helpers = [f for f in _scan() if f[3] == "resume_refuse"]
    assert helpers, "the resume_refuse helper stop was not found"
    for _rel, _rule, _anchor, _helper, callers in helpers:
        assert "_drive_resume" in callers, callers


SHELL_INJECTIONS = {
    "exit_nonzero": "  exit 1",
    "return_nonzero": "  return 1",
    "drive_rc_nonzero": "  _DRIVE_RC=1",
    "drive_rc_zero_refusal": "  log 'refusing to launch'\n  _DRIVE_RC=0",
    "halt_marker": "  printf 'x\\n' >\"$HALT\"",
    "resume_refuse": '  resume_refuse "$target" "$reason"',
}

PY_INJECTIONS = {
    "raise_fail": "    raise Fail('x')",
    "raise_config": "    raise ConfigError('x')",
    "raise_coordination": "    raise CoordinationError('x')",
    "raise_fenced": "    raise ReservationFenced('x')",
    "raise_busy": "    raise PublicationBusy('x')",
    "blocked_call": "    return blocked(info, 'x')",
    "ok_false": "    return {'ok': False}",
}


def _unexpected(driver: Path, gitsync: Path) -> list[tuple[str, str, str]]:
    allowed = _allowed()
    return [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan(
            driver=driver, gitsync=gitsync
        )
        if (rel, rule, anchor) not in allowed
    ]


def test_an_injected_stop_fails_the_tripwire(tmp_path: Path) -> None:
    driver = tmp_path / "driver"
    driver.mkdir()
    (driver / "lib-driver-new.sh").write_text("new_helper() { return 1; }\n")
    injected = _unexpected(driver, tmp_path)
    assert (
        "driver/lib-driver-new.sh",
        "return_nonzero",
        "fn:new_helper|new_helper() { return 1; }",
    ) in injected


@pytest.mark.parametrize("rule", sorted(SHELL_INJECTIONS))
def test_a_shell_stop_in_an_existing_file_fails(tmp_path: Path, rule: str) -> None:
    driver = tmp_path / "driver"
    driver.mkdir()
    source = (DRIVER / "lib-driver-run.sh").read_text()
    body = SHELL_INJECTIONS[rule]
    (driver / "lib-driver-run.sh").write_text(
        source + f"\nnew_helper() {{\n{body}\n}}\n"
    )
    injected = _unexpected(driver, tmp_path)
    assert any(found_rule == rule for _rel, found_rule, _anchor in injected), injected


@pytest.mark.parametrize("rule", sorted(PY_INJECTIONS))
def test_a_python_stop_in_an_existing_file_fails(tmp_path: Path, rule: str) -> None:
    gitsync = tmp_path / "gitsync"
    gitsync.mkdir()
    source = (GITSYNC / "merge.py").read_text()
    body = PY_INJECTIONS[rule]
    (gitsync / "merge.py").write_text(source + f"\ndef new_helper():\n{body}\n")
    injected = _unexpected(tmp_path, gitsync)
    assert any(found_rule == rule for _rel, found_rule, _anchor in injected), injected
