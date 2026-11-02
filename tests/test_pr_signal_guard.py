# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The host reply check: a configured command reads the draft reply and can refuse it."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.config.render import shell_lines
from argus.gitsync import pr_signal_reply
from argus.gitsync.pr_signal_reply import reply_to_comment
from config_fixture import BASE, write
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


def _configured(tmp_path: Path, **commands: str) -> Path:
    """A config file with the given commands, loaded through the real loader."""
    document = copy.deepcopy(BASE)
    document["commands"].update(commands)
    path = write(tmp_path, json.dumps(document))
    conventions.configure_from_cli(["--config", str(path)])
    return path


def test_a_config_file_loads_the_reply_check_and_a_reply_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a real config file and a real check command, when a plain reply is sent,
    # then the check runs on the draft through the loaded value, and the reply posts.
    record(tmp_path, {"10": comment(10)})
    posts = spy_gh(monkeypatch)
    seen = tmp_path / "seen.txt"
    check = tmp_path / "check.py"
    check.write_text(
        f"import sys\nopen({str(seen)!r}, 'w').write(sys.stdin.read())\n", "utf-8"
    )
    previous = conventions.override({})
    try:
        _configured(tmp_path, **{"reply-check": f"{sys.executable} {check}"})
        assert conventions.setting("commands.reply-check") == (
            f"{sys.executable} {check}"
        )
        out = reply_to_comment(
            tmp_path, REF, 10, text="Done.", fetch=fresh({"10": comment(10)})
        )
    finally:
        conventions.configure(previous)
    assert out["ok"] is True, out
    assert seen.read_text("utf-8") == "Done."
    assert len(posts) == 1


def test_a_config_file_with_no_reply_check_leaves_it_unset(tmp_path: Path) -> None:
    previous = conventions.override({})
    try:
        path = _configured(tmp_path)
        assert conventions.setting("commands.reply-check", None) is None
        document = json.loads(path.read_text("utf-8"))
        document["commands"]["reply-check"] = "check-reply --flag"
        path.write_text(json.dumps(document), "utf-8")
        lines = shell_lines(conventions.configure_from_cli(["--config", str(path)]))
        assert any("CMD_REPLY_CHECK" in line for line in lines)
    finally:
        conventions.configure(previous)
