# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The issue pull request is recorded on the claim when its draft is opened."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationError
from argus.issues import pr_record
from issues_fixture import settings

BRANCH = "issue/100001-fix-the-input-2"
URL = "https://github.com/example/project/pull/71"


class Recorder:
    """A board stand-in that records the pull request writes it receives."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.writes: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def set_issue_pull_request(
        self, token: dict[str, Any], pull_request: dict[str, Any]
    ) -> None:
        if self.refuse:
            raise CoordinationError("board is busy")
        self.writes.append((token, pull_request))


@pytest.fixture
def config(tmp_path: Path) -> Iterator[None]:
    previous = conventions.override(settings(tmp_path, tmp_path / "gh"))
    yield
    conventions.configure(previous)


def use(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> None:
    def store(*_a: object, **_k: object) -> Recorder:
        return recorder

    monkeypatch.setattr(pr_record, "ReservationStore", store)


@pytest.mark.usefixtures("config")
def test_a_run_with_a_claim_records_its_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given this run holds a claim, when its draft pull request opens, then the branch
    # and URL are written to the board under that claim, at once.
    recorder = Recorder()
    use(monkeypatch, recorder)
    monkeypatch.setenv("CLIO_RESERVATION_ID", "rsv-7")
    monkeypatch.setenv("CLIO_RESERVATION_GENERATION", "3")
    assert pr_record.record_pull_request(tmp_path, BRANCH, URL) is True
    token, value = recorder.writes[0]
    assert token == {
        "issue": "100001",
        "machine_id": "worker-one",
        "reservation_id": "rsv-7",
        "generation": 3,
    }
    assert value == {"branch": BRANCH, "url": URL}


@pytest.mark.usefixtures("config")
def test_a_run_without_a_claim_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given no claim token in the environment, when the draft opens, then nothing is
    # written here; the tick records the pull request later.
    recorder = Recorder()
    use(monkeypatch, recorder)
    monkeypatch.delenv("CLIO_RESERVATION_ID", raising=False)
    assert pr_record.record_pull_request(tmp_path, BRANCH, URL) is False
    assert recorder.writes == []


@pytest.mark.usefixtures("config")
def test_a_branch_that_is_not_an_issue_branch_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a branch that is not an issue branch, when the draft opens, then nothing
    # is written.
    recorder = Recorder()
    use(monkeypatch, recorder)
    monkeypatch.setenv("CLIO_RESERVATION_ID", "rsv-7")
    monkeypatch.setenv("CLIO_RESERVATION_GENERATION", "3")
    assert pr_record.record_pull_request(tmp_path, "feature/x", URL) is False
    assert recorder.writes == []


@pytest.mark.usefixtures("config")
def test_a_refused_write_returns_false_and_raises_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the board refuses the write, when the draft opens, then the call returns
    # False, so the publication still completes.
    use(monkeypatch, Recorder(refuse=True))
    monkeypatch.setenv("CLIO_RESERVATION_ID", "rsv-7")
    monkeypatch.setenv("CLIO_RESERVATION_GENERATION", "3")
    assert pr_record.record_pull_request(tmp_path, BRANCH, URL) is False
