# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The host reply check: a configured command reads the draft reply and can refuse it."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.gitsync import pr_signal_reply
from argus.gitsync.pr_signal_reply import reply_to_comment
from reply_fixture import REF, comment, fresh, record, spy_gh


def test_refuses_when_the_host_reply_check_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record(tmp_path, {"10": comment(10)})
    posts = spy_gh(monkeypatch)

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(pr_signal_reply.subprocess, "run", fake_run)
    previous = conventions.override({"commands.reply-check": "check-reply"})
    try:
        out = reply_to_comment(
            tmp_path, REF, 10, text="Done.", fetch=fresh({"10": comment(10)})
        )
    finally:
        conventions.configure(previous)
    assert out["state"] == "REFUSED"
    assert "refused this reply" in out["reasons"][0]
    assert posts == []


def test_posts_after_the_host_reply_check_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record(tmp_path, {"10": comment(10)})
    posts = spy_gh(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(pr_signal_reply.subprocess, "run", fake_run)
    previous = conventions.override({"commands.reply-check": "check-reply --flag"})
    try:
        out = reply_to_comment(
            tmp_path, REF, 10, text="Done.", fetch=fresh({"10": comment(10)})
        )
    finally:
        conventions.configure(previous)
    assert out["state"] == "POSTED"
    assert len(posts) == 1
    assert calls[0]["argv"] == ["check-reply", "--flag"]
    assert calls[0]["input"] == "Done."
    assert calls[0]["timeout"] == pr_signal_reply.REPLY_CHECK_TIMEOUT


def test_refuses_when_the_host_reply_check_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record(tmp_path, {"10": comment(10)})
    posts = spy_gh(monkeypatch)
    previous = conventions.override(
        {"commands.reply-check": "definitely-missing-argus-reply-check"}
    )
    try:
        out = reply_to_comment(
            tmp_path, REF, 10, text="Done.", fetch=fresh({"10": comment(10)})
        )
    finally:
        conventions.configure(previous)
    assert out["state"] == "REFUSED"
    assert "could not run" in out["reasons"][0]
    assert posts == []


@pytest.mark.parametrize("command", ["   ", "'unclosed"])
def test_refuses_when_the_host_reply_check_command_is_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    record(tmp_path, {"10": comment(10)})
    posts = spy_gh(monkeypatch)
    previous = conventions.override({"commands.reply-check": command})
    try:
        out = reply_to_comment(
            tmp_path, REF, 10, text="Done.", fetch=fresh({"10": comment(10)})
        )
    finally:
        conventions.configure(previous)
    assert out["state"] == "REFUSED"
    assert "could not run" in out["reasons"][0]
    assert posts == []
