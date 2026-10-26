# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Report retries stay idempotent after GitHub accepted a timed-out write."""

import hashlib
from pathlib import Path

import pytest

from argus import conventions
from argus.issues.client import IssueClient
from issues_fixture import argv, fake_gh, issue, settings


def test_report_retry_does_not_post_the_same_payload_twice(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    try:
        report = tmp_path / "report.md"
        report.write_bytes(b"The input fix is ready for review.\n")
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        client = IssueClient()
        client.comment(100001, report, report=True, expected_sha256=digest)
        client.comment(100001, report, report=True, expected_sha256=digest)
        assert (
            len(
                [
                    call
                    for call in argv(tmp_path)
                    if call["argv"][:2] == ["issue", "comment"]
                ]
            )
            == 1
        )
    finally:
        conventions.configure(previous)
