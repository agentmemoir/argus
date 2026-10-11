#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The published folder must carry no identifier of any kind.

Argus is on its way into a public repository: a package anyone can install,
generic to any project. Its author's accounts, machines, paths and personal
names must not appear in it, not even as a default a machine falls back to. The
two values this issue removed - a Hermes profile name and a Discord channel id,
both written into the driver as the defaults for the notification settings - are
gone, and this file is what stops the class from returning quietly.

A gate, not a one-off sweep. It reads every file the package ships, and it runs
from ``make self-test``, so an identifier added to the published folder fails the
checks instead of shipping. The same scan runs on its own:

    python3 tests/test_identifiers.py

What it matches
---------------
The patterns are structural on purpose. Spelling the author's own account or
machine name inside a published scanner would itself publish the name it exists
to keep out, so each pattern matches a shape instead, and each shape is one this
folder is expected to hold none of:

* a home directory that names its user (``/Users/<name>/``, ``/home/<name>/``,
  ``C:\\Users\\<name>``);
* a notification or account id: a run of sixteen or more digits that stands
  outside a hexadecimal token, such as a Discord snowflake. A run of one
  repeated digit (``0000...``) is the documented placeholder and is not an id,
  and a run inside a 32-character-or-longer hexadecimal token is a hash, such
  as the digests every lock file carries;
* a notification setting given a value as its default inside the driver
  (``HERMES_PROFILE_NAME:=someone``). ``:-`` writes an empty default and is the
  shape the driver uses, so it is allowed;
* a notification profile or channel written as a literal in a shipped file.
  Only the documented placeholders may appear, because a real target belongs to
  whoever receives the notices and lives in that machine's own configuration
  file, which is not part of the package.

What it cannot match is a bare personal name or a bare account handle with no
surrounding shape. Those are found by reading, not by a pattern; the two here
were removed by hand. That limit is recorded rather than papered over, so a green
gate is never read as proof that no name of any kind is present.

The scan runs as a test rather than only as a pre-commit step, because a gate
nobody runs is not a gate. A companion test proves each pattern still fires, so a
green scan means "no identifiers" and not "no rules".
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: This file and the repository root its scan covers.
SELF = Path(__file__).resolve()
HERE = SELF.parents[1]

#: Directories that are not part of the published package.
SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".pytest_cache", "dist", "build", ".venv", "target"}
)

#: The only notification targets a shipped file may name. A real one belongs to
#: the machine that receives the notices, in a file that is not part of the
#: package; these are the placeholders the sample and the fixtures use.
PROFILE_PLACEHOLDERS = frozenset({"", "example-profile", "fixture-profile"})
CHANNEL_PLACEHOLDERS = frozenset({"", "discord:000000000000000000"})

#: A home directory that names its user.
HOME_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+"
)

#: A notification or account id: sixteen or more digits in a row.
LONG_ID = re.compile(r"(?<!\d)(\d{16,})(?!\d)")

#: A long hexadecimal token: a hash or checksum, not an account id. A lock file
#: carries many of them, and a 64-character digest often contains a run of
#: sixteen digits by chance.
HEX_TOKEN = re.compile(r"\b[0-9a-fA-F]{32,}\b")

#: A driver default that writes a value into a notification setting. ``:-`` is
#: an empty default; only ``:=`` with something after it is a value.
NOTIFY_DEFAULT = re.compile(
    r"\b(?:HERMES_PROFILE_NAME|DISCORD_CHANNEL|DISCORD_FALLBACK_CHANNEL):=[^}]"
)

#: A notification profile or channel written as a literal in a shipped file.
PROFILE_LITERAL = re.compile(r'"profile"\s*:\s*"([^"]*)"')
CHANNEL_LITERAL = re.compile(r'"channel"\s*:\s*"([^"]*)"')


def _is_placeholder_digits(digits: str) -> bool:
    """A run of one repeated digit is the placeholder shape, not an id."""
    return len(set(digits)) == 1


def _inside_hex_token(line: str, start: int, end: int) -> bool:
    """Whether the match lies inside a hexadecimal hash."""
    return any(
        token.start() <= start and end <= token.end()
        for token in HEX_TOKEN.finditer(line)
    )


def scan(name: str, text: str) -> list[str]:
    """Every finding in one file, each as ``file:line: what``."""
    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in HOME_PATH.finditer(line):
            findings.append(f"{name}:{number}: home path {match.group(0)!r}")
        for match in LONG_ID.finditer(line):
            if _is_placeholder_digits(match.group(1)):
                continue
            if _inside_hex_token(line, match.start(1), match.end(1)):
                continue
            findings.append(f"{name}:{number}: numeric id {match.group(1)!r}")
        for match in NOTIFY_DEFAULT.finditer(line):
            findings.append(f"{name}:{number}: notification default {match.group(0)!r}")
        for match in PROFILE_LITERAL.finditer(line):
            if match.group(1) not in PROFILE_PLACEHOLDERS:
                findings.append(
                    f"{name}:{number}: notification profile {match.group(1)!r}"
                )
        for match in CHANNEL_LITERAL.finditer(line):
            if match.group(1) not in CHANNEL_PLACEHOLDERS:
                findings.append(
                    f"{name}:{number}: notification channel {match.group(1)!r}"
                )
    return findings


def _shipped_paths() -> set[Path] | None:
    """The paths git would ship, or ``None`` when git cannot answer.

    A file git ignores is a machine-local artifact, not part of the package, so a
    scan of "every file the package ships" must not read it. The skills tooling
    writes ``skills-lock.json`` and ``.agents/`` into the checkout, and a machine
    that keeps them out of git puts them in its global ignore file; both are
    local, and both carry ids and machine paths this gate must not read as
    published content. An unpacked source tree has no git to ask, and there the
    folder really is all there is, so the caller falls back to reading every file.

    The cost is that the gate trusts the ignore rules: a broad rule added to
    ``.gitignore`` or the global ignore file stops the scan reading the files it
    matches, and nothing here can fail on that. The alternative, reading local
    artifacts as shipped content, fails on a working machine, which is worse.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(HERE),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {HERE / line for line in result.stdout.splitlines() if line}


def published_files() -> list[Path]:
    """Every file the package ships, this scanner excepted.

    This file names the shapes it forbids, so it is the one file the scan skips.
    Nothing else is skipped, because the package ships the folder as a whole; the
    one exception is a path git ignores, which is a local artifact and not part
    of the package.
    """
    shipped = _shipped_paths()
    files: list[Path] = []
    for path in sorted(HERE.rglob("*")):
        if not path.is_file() or path == SELF:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(HERE).parts):
            continue
        if shipped is not None and path not in shipped:
            continue
        files.append(path)
    return files


def _text(path: Path) -> str:
    """A file's text, or nothing when it cannot be read as text."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def check(condition: object, detail: object = "") -> None:
    if not condition:
        raise AssertionError(detail)


def test_published_folder_has_no_identifier():
    findings: list[str] = []
    for path in published_files():
        findings.extend(scan(str(path.relative_to(HERE)), _text(path)))
    check(not findings, "identifiers in the published folder:\n" + "\n".join(findings))


def test_the_patterns_fire_and_do_not_overreach():
    """A pattern that stopped matching would make the scan vacuous.

    Each bad sample is a shape the folder must not hold. Each safe sample is a
    shape it legitimately does hold, so a pattern that cries wolf is caught here
    rather than by a reader who learns to skip the gate.
    """
    cases = (
        ("/Users/example/notes.md", 1),  # a home path
        ("/home/example/work", 1),  # a home path
        ("discord:1234567890123456789", 1),  # an id, not a placeholder
        ("id1234567890123456789", 1),  # an id shape in a short token
        ("HERMES_PROFILE_NAME:=someone", 1),  # a value written as a default
        ('"profile": "someone-here"', 1),  # a real profile literal
        ('"channel": "someone-channel"', 1),  # a real channel literal
        ("/opt/homebrew/bin/python3", 0),  # a tool path, not a home
        ("~/code/project", 0),  # no login name
        ("discord:000000000000000000", 0),  # the placeholder id
        ("phase 100010", 0),  # six digits is a phase number
        (
            "sha256:5b976054553670829985ed767feb78fb6bcede0175327c4844dd5c281c1be659",
            0,
        ),  # a digest, not an id
        (
            "https://files.example.org/packages/1b/d7/"
            "c56955934a431a0fa3e4e9aa7af4a53ceab2a61241005427545208945eb4/tool.whl",
            0,
        ),  # a URL path hash, not an id
        ('PROFILE="${HERMES_PROFILE_NAME:-}"', 0),  # an empty default
        ('"profile": "example-profile"', 0),  # a documented placeholder
        ('"channel": "discord:000000000000000000"', 0),
        ("an ordinary sentence about phases, ids and profiles", 0),
        ("the roadmap folder is /srv/roadmap", 0),
    )
    for sample, expected in cases:
        actual = len(scan("fixture", sample))
        check(
            actual == expected, f"{sample!r}: the scan found {actual}, want {expected}"
        )


TESTS = (
    ("published folder has no identifier", test_published_folder_has_no_identifier),
    ("patterns fire and do not overreach", test_the_patterns_fire_and_do_not_overreach),
)


def main() -> int:
    for name, test in TESTS:
        try:
            test()
        except AssertionError as exc:
            print(f"FAIL {name}: {exc}")
            return 1
    print("test_identifiers: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
