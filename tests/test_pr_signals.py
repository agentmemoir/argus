# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Pull request signals: the snapshot read, the diff, and the watch that records changes."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import pr_signal_fetch
from argus.gitsync.pr_signal_diff import diff_snapshots
from argus.gitsync.pr_signal_store import EVENTS_FILE, SNAPSHOT_FILE, read_snapshot
from argus.gitsync.pr_signals import watch_pull_request

REF = {"host": None, "owner": "example-org", "repo": "example-repo", "number": 7}


def _alert(number: int, state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "rule": {"id": "rust/hard-coded-cryptographic-value", "severity": "critical"},
        "most_recent_instance": {"location": {"path": "src/a.rs", "start_line": 9}},
        "html_url": f"https://example.invalid/alerts/{number}",
    }


def _comment(cid: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": cid,
        "user": {"login": "reviewer"},
        "path": "src/a.rs",
        "line": 9,
        "in_reply_to_id": None,
        "updated_at": "2026-10-13T12:00:00Z",
        "reactions": {"total_count": 0},
        "body": "Hard-coded salt.",
    }
    row.update(overrides)
    return row


def _snapshot(alerts: list[dict[str, Any]], comments: list[dict[str, Any]]) -> Any:
    """The normalized shape ``fetch_snapshot`` returns, built from raw rows."""
    return {
        "alerts": {str(a["number"]): pr_signal_fetch._alert(a) for a in alerts},
        "comments": {str(c["id"]): pr_signal_fetch._comment(c) for c in comments},
    }


def test_diff_is_empty_when_nothing_changed() -> None:
    snap = _snapshot([_alert(1)], [_comment(10)])
    assert diff_snapshots(snap, snap) == []


def test_diff_reports_every_item_when_there_is_no_previous_snapshot() -> None:
    snap = _snapshot([_alert(1)], [_comment(10)])
    kinds = [event["kind"] for event in diff_snapshots(None, snap)]
    assert kinds == ["alert_opened", "comment_added"]


def test_diff_reports_a_new_alert_and_a_state_change() -> None:
    before = _snapshot([_alert(1)], [])
    after = _snapshot([_alert(1, state="fixed"), _alert(2)], [])
    events = diff_snapshots(before, after)
    assert events[0] == {
        "kind": "alert_state",
        "number": "1",
        "from": "open",
        "to": "fixed",
    }
    assert events[1]["kind"] == "alert_opened"
    assert events[1]["number"] == "2"


def test_diff_reports_edits_reactions_and_removals_of_comments() -> None:
    before = _snapshot([], [_comment(10), _comment(11)])
    edited = _comment(
        10, updated_at="2026-10-13T13:00:00Z", reactions={"total_count": 2}
    )
    after = _snapshot([], [edited])
    events = diff_snapshots(before, after)
    assert [event["kind"] for event in events] == [
        "comment_edited",
        "comment_reactions",
        "comment_removed",
    ]
    assert events[1]["from"] == 0 and events[1]["to"] == 2


def test_fetch_snapshot_normalizes_alerts_and_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_gh(argv: list[str], *_args: object) -> Any:
        calls.append(argv)
        rows = [[_alert(1)]] if "code-scanning/alerts" in argv[-1] else [[_comment(10)]]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(rows)), ""

    monkeypatch.setattr(pr_signal_fetch, "_gh", fake_gh)
    snap, why = pr_signal_fetch.fetch_snapshot("example-org", "example-repo", 7)
    assert why == ""
    assert snap is not None
    assert snap["alerts"]["1"]["severity"] == "critical"
    assert snap["alerts"]["1"]["line"] == 9
    assert snap["comments"]["10"]["author"] == "reviewer"
    assert "state=all&ref=refs/pull/7/head" in calls[0][-1]


def test_fetch_snapshot_returns_no_snapshot_when_gh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_gh(argv: list[str], *_args: object) -> Any:
        return subprocess.CompletedProcess(argv, 1, stdout=""), ""

    monkeypatch.setattr(pr_signal_fetch, "_gh", failing_gh)
    snap, why = pr_signal_fetch.fetch_snapshot("example-org", "example-repo", 7)
    assert snap is None
    assert "exited 1" in why


def test_watch_keeps_the_last_snapshot_when_the_read_fails(tmp_path: Path) -> None:
    previous = _snapshot([_alert(1)], [])
    (tmp_path / SNAPSHOT_FILE).write_text(json.dumps(previous))

    def unavailable(*_args: object) -> Any:
        return None, "gh did not answer within 60s"

    out = watch_pull_request(tmp_path, REF, fetch=unavailable)
    assert out["state"] == "UNAVAILABLE"
    assert read_snapshot(tmp_path) == previous
    assert not (tmp_path / EVENTS_FILE).exists()


def test_watch_records_each_change_once(tmp_path: Path) -> None:
    alerts, comments = [_alert(1)], [_comment(10)]

    def reading(*_args: object) -> Any:
        return _snapshot(alerts, comments), ""

    first = watch_pull_request(tmp_path, REF, fetch=reading)
    second = watch_pull_request(tmp_path, REF, fetch=reading)
    assert [e["kind"] for e in first["events"]] == ["alert_opened", "comment_added"]
    assert second["events"] == []
    lines = (tmp_path / EVENTS_FILE).read_text().splitlines()
    assert len(lines) == 2
    assert all("at" in json.loads(line) for line in lines)
