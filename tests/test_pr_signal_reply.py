# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The one write path for pull request threads: it refuses unless the recorded snapshot and a fresh read agree."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import pr_signal_reply
from argus.gitsync.pr_signal_reply import reply_to_comment
from argus.gitsync.pr_signal_store import SNAPSHOT_FILE, WRITES_FILE

REF = {"host": None, "owner": "example-org", "repo": "example-repo", "number": 7}


def _comment(_cid: int, in_reply_to: int | None = None, body: str = "Fix it.") -> Any:
    return {
        "author": "reviewer",
        "path": "src/a.rs",
        "line": 9,
        "in_reply_to": in_reply_to,
        "updated_at": "t",
        "reactions": 0,
        "trusted": True,
        "body": body,
    }


def _snapshot(comments: dict[str, Any]) -> dict[str, Any]:
    return {"alerts": {}, "comments": comments}


def _record(run_dir: Path, comments: dict[str, Any]) -> None:
    (run_dir / SNAPSHOT_FILE).write_text(json.dumps(_snapshot(comments)))


def _fresh(comments: dict[str, Any]) -> Any:
    def read(*_args: object) -> Any:
        return _snapshot(comments), ""

    return read


def _no_read(*_args: object) -> Any:
    return None, "gh did not answer within 60s"


def _spy_gh(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
    posts: list[list[str]] = []

    def fake_gh(argv: list[str], *_args: object) -> Any:
        posts.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout=""), ""

    monkeypatch.setattr(pr_signal_reply, "_gh", fake_gh)
    return posts


def test_refuses_without_a_recorded_snapshot(tmp_path: Path) -> None:
    out = reply_to_comment(tmp_path, REF, 10, text="Done.", fetch=_fresh({}))
    assert out["state"] == "REFUSED"
    assert "watch the pull request first" in out["reasons"][0]


def test_refuses_a_comment_that_was_not_recorded(tmp_path: Path) -> None:
    _record(tmp_path, {"10": _comment(10)})
    out = reply_to_comment(tmp_path, REF, 99, text="Done.", fetch=_fresh({}))
    assert out["state"] == "REFUSED"
    assert "not in the recorded snapshot" in out["reasons"][0]


def test_refuses_a_reply_to_a_reply(tmp_path: Path) -> None:
    _record(tmp_path, {"11": _comment(11, in_reply_to=10)})
    out = reply_to_comment(tmp_path, REF, 11, text="Done.", fetch=_fresh({}))
    assert out["state"] == "REFUSED"
    assert "replies to replies" in out["reasons"][0]


def test_refuses_when_the_fresh_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    out = reply_to_comment(tmp_path, REF, 10, text="Done.", fetch=_no_read)
    assert out["state"] == "REFUSED"
    assert "could not re-read" in out["reasons"][0]
    assert posts == []


def test_refuses_a_comment_gone_from_the_fresh_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    out = reply_to_comment(tmp_path, REF, 10, text="Done.", fetch=_fresh({}))
    assert out["state"] == "REFUSED"
    assert posts == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({}, "exactly one"),
        ({"text": "a", "reaction": "eyes"}, "exactly one"),
        ({"reaction": "merge"}, "reaction must be one of"),
    ],
)
def test_refuses_bad_arguments_before_any_read(
    tmp_path: Path, kwargs: dict[str, Any], reason: str
) -> None:
    out = reply_to_comment(tmp_path, REF, 10, fetch=_no_read, **kwargs)
    assert out["state"] == "REFUSED"
    assert reason in out["reasons"][0]


def test_posts_one_reply_to_the_recorded_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    out = reply_to_comment(
        tmp_path,
        REF,
        10,
        text="Fixed in one commit.",
        fetch=_fresh({"10": _comment(10)}),
    )
    assert out["state"] == "POSTED"
    assert posts == [
        [
            "api",
            "-X",
            "POST",
            "repos/example-org/example-repo/pulls/7/comments/10/replies",
            "-f",
            "body=Fixed in one commit.",
        ]
    ]
    logged = json.loads((tmp_path / WRITES_FILE).read_text())
    assert logged["state"] == "POSTED" and logged["kind"] == "reply"


def test_posts_one_reaction_to_the_recorded_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    out = reply_to_comment(
        tmp_path, REF, 10, reaction="eyes", fetch=_fresh({"10": _comment(10)})
    )
    assert out["state"] == "POSTED"
    assert posts[0][3] == "repos/example-org/example-repo/pulls/comments/10/reactions"
    assert posts[0][-1] == "content=eyes"


def test_skips_a_reply_that_is_already_on_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    fresh = {"10": _comment(10), "12": _comment(12, in_reply_to=10, body="Done.")}
    out = reply_to_comment(tmp_path, REF, 10, text="Done.", fetch=_fresh(fresh))
    assert out["state"] == "ALREADY-POSTED"
    assert posts == []


def test_a_failed_post_is_logged_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    _spy_gh(monkeypatch, returncode=22)
    out = reply_to_comment(
        tmp_path, REF, 10, reaction="eyes", fetch=_fresh({"10": _comment(10)})
    )
    assert out["state"] == "FAILED"
    assert out["ok"] is False
    logged = json.loads((tmp_path / WRITES_FILE).read_text())
    assert logged["state"] == "FAILED"


def test_refuses_a_comment_whose_author_is_not_trusted(tmp_path: Path) -> None:
    _record(tmp_path, {"10": {**_comment(10), "trusted": False}})
    out = reply_to_comment(tmp_path, REF, 10, text="Done.", fetch=_fresh({}))
    assert out["state"] == "REFUSED"
    assert "not trusted" in out["reasons"][0]


def test_refuses_reply_text_longer_than_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    too_long = "x" * (pr_signal_reply.MAX_REPLY_CHARS + 1)
    out = reply_to_comment(
        tmp_path, REF, 10, text=too_long, fetch=_fresh({"10": _comment(10)})
    )
    assert out["state"] == "REFUSED"
    assert posts == []


def test_refuses_reply_text_that_looks_like_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    fake_token = "gh" + "p_" + "A" * 36
    out = reply_to_comment(
        tmp_path,
        REF,
        10,
        text=f"token {fake_token}",
        fetch=_fresh({"10": _comment(10)}),
    )
    assert out["state"] == "REFUSED"
    assert "credential" in out["reasons"][0]
    assert posts == []


def test_a_repeat_of_a_posted_reply_is_skipped_from_the_writes_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(tmp_path, {"10": _comment(10)})
    posts = _spy_gh(monkeypatch)
    first = reply_to_comment(
        tmp_path, REF, 10, text="Done.", fetch=_fresh({"10": _comment(10)})
    )
    second = reply_to_comment(
        tmp_path, REF, 10, text="Done.", fetch=_fresh({"10": _comment(10)})
    )
    assert first["state"] == "POSTED"
    assert second["state"] == "ALREADY-POSTED"
    assert len(posts) == 1
