# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The leak-scan patterns prove themselves against fixed samples.

Source: ``tests/test_no_host_leak.py``. That file owns the scan; this file owns
the proof that its patterns are live and honest. A pattern that stopped
matching would make its own scan vacuous, and a pattern that overreaches is a
scan people learn to skip -- which is how a real credential gets committed.
"""

from __future__ import annotations

import re

import pytest

from test_no_host_leak import HOST_PATH, SECRET


@pytest.mark.parametrize(
    ("pattern", "sample"),
    [
        (HOST_PATH, "see private/example-host/plans/run.py"),
        (SECRET, 'API_KEY = "abcdef123456"'),
        (SECRET, "password: hunter2hunter2"),
    ],
)
def test_each_pattern_actually_matches(pattern: re.Pattern[str], sample: str) -> None:
    """A pattern that stopped matching would make its own test vacuous."""
    assert pattern.search(sample), f"{pattern.pattern} no longer matches {sample!r}"


@pytest.mark.parametrize(
    "sample",
    [
        "an ordinary sentence about phases and tokens",
        "phase 100010 is the lowest free item",
        "work-100010-cache.md needs a branch slug",
        "a private checkout and a public one",
        "the private tree",
        "token = match.group(1)",
        "token = load_reservation_file(record)",
        'reason="token=secret-value"',
        "self.token_secret_cache = compute()",
        "HARNESS_TOKEN = 'harness'",
        "the phases folder is /srv/plans",
        "see docs/program-files.md for the map",
        "api_key_here_is_a_name_not_a_value",
    ],
)
def test_the_patterns_do_not_overreach(sample: str) -> None:
    """A leak scan that cries wolf is a scan people learn to skip.

    Each sample is something an honest sentence about this engine could say.
    None may match.
    """
    assert not HOST_PATH.search(sample)
    assert not SECRET.search(sample)
