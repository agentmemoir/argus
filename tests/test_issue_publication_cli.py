# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The issue CLI dispatch and the default phase output are separate."""

import json
import sys

import pytest

from argus import conventions
from argus.gitsync import main
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)


def _keep_settings(_args: object) -> None:
    return None


def test_cli_branches_the_issue_namespace(
    publication: Publication,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _token, task = publication.claim(1234567)
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--kind",
            "issue",
            "--mode",
            "branch",
            "--phase",
            "1234567",
            "--roadmap",
            str(task),
        ],
    )
    assert main.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["branch"] == "issue/1234567-fix-input"
    assert result["kind"] == "issue"


def test_default_phase_cli_output_is_byte_identical(
    publication: Publication,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    argv = ["gitsync", "--root", str(publication.root), "--mode", "tidy"]
    monkeypatch.setattr(sys, "argv", argv)
    assert main.main() == 0
    default = capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", argv + ["--kind", "phase"])
    assert main.main() == 0
    assert capsys.readouterr().out == default


def test_phase_published_mode_is_not_available_for_issues(
    publication: Publication,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--root",
            str(publication.root),
            "--kind",
            "issue",
            "--mode",
            "published",
            "--phase",
            "1234567",
        ],
    )
    assert main.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "config_error"
