# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Member-by-member publish sequences against a stateful gh stand-in."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.group_review import _group_pending, update_group_pull_request
from argus.gitsync.selftest_fixture import BOARD_REL
from argus.gitsync.selftest_group_review import _receipt_fixture, _write_receipt


class _FakeGitHub:
    """A stateful stand-in for gh across one member sequence: one body, a comment log."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.comments: list[str] = []
        self.fail_view = False
        self.fail_edit = False
        self.fail_comment = False

    def route(self, argv: Any, **_kwargs: Any) -> Any:
        import types

        cmd = str(argv[2]) if len(argv) > 2 else ""
        if cmd == "comment":
            if self.fail_comment:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            self.comments.append(str(argv[argv.index("--body") + 1]))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == "view":
            if self.fail_view:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            payload: dict[str, Any] = {"body": self.body}
            if "comments" in argv:
                payload = {"comments": [{"body": text} for text in self.comments]}
            return types.SimpleNamespace(
                returncode=0, stdout=json.dumps(payload), stderr=""
            )
        if cmd == "edit":
            if self.fail_edit:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            self.body = str(argv[argv.index("--body") + 1])
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call: {argv!r}")


def _sequence_fixture(base: Path, name: str) -> tuple[Path, str]:
    pub = _receipt_fixture(base, name)
    phases = pub / BOARD_REL / "phases"
    phases.mkdir(parents=True, exist_ok=True)
    (phases / "phase-100020-cache-warm.md").write_text("# 20\n", encoding="utf-8")
    (phases / "phase-100030-index-build.md").write_text("# 30\n", encoding="utf-8")
    url = "https://github.com/example-org/example-repo/pull/7"
    _write_receipt(
        pub,
        100020,
        state="awaiting_review",
        branch="b",
        group=["100020", "100030"],
        pull_request=url,
        public_head="abc1234def",
    )
    return pub, url


def _publish_sequence_checks(
    base: Path, results: list[tuple[str, object, str]]
) -> None:
    pub, url = _sequence_fixture(base, "group-sequence")
    members = ["100020", "100030"]
    fake = _FakeGitHub("Plan\n- [ ] cache warm\n- [ ] index build\n")
    saved: Any = subprocess.run
    subprocess.run = fake.route  # type: ignore[assignment]
    try:
        first = update_group_pull_request(pub, 100020, members, url)
        first_body = fake.body
        first_comments = list(fake.comments)
        _write_receipt(
            pub,
            100030,
            state="awaiting_review",
            branch="b",
            group=members,
            pull_request=url,
            public_head="def5678abc",
        )
        second = update_group_pull_request(pub, 100030, members, url)
        second_comments = list(fake.comments)
        repeat = update_group_pull_request(pub, 100030, members, url)
    finally:
        subprocess.run = saved
    results.append(
        (
            "the first publish ticks only its own box and comments once",
            isinstance(first, dict)
            and first.get("ok") is True
            and "- [x] cache warm" in first_body
            and "- [ ] index build" in first_body
            and len(first_comments) == 1,
            f"first={first} comments={first_comments}",
        )
    )
    results.append(
        (
            "the last publish ticks every box and leaves two comments",
            isinstance(second, dict)
            and second.get("ok") is True
            and "- [x] cache warm" in fake.body
            and "- [x] index build" in fake.body
            and len(fake.comments) == 2,
            f"second={second} body={fake.body!r}",
        )
    )
    results.append(
        (
            "publishing the same head again posts no second comment",
            isinstance(repeat, dict)
            and repeat.get("ok") is True
            and repeat.get("comment_detail") == "comment already posted"
            and fake.comments == second_comments,
            f"repeat={repeat} comments={fake.comments}",
        )
    )
    results.append(
        (
            "no member is pending after the last publish, so ready fires",
            _group_pending(pub, members, 100030) == [],
            f"pending={_group_pending(pub, members, 100030)}",
        )
    )


def _failure_ordering_checks(
    base: Path, results: list[tuple[str, object, str]]
) -> None:
    members = ["100020", "100030"]
    pub, url = _sequence_fixture(base, "group-failures")
    edit_down = _FakeGitHub("Plan\n- [ ] cache warm\n- [ ] index build\n")
    edit_down.fail_edit = True
    saved: Any = subprocess.run
    subprocess.run = edit_down.route  # type: ignore[assignment]
    try:
        refused = update_group_pull_request(pub, 100020, members, url)
    finally:
        subprocess.run = saved
    results.append(
        (
            "a failed body edit posts no comment",
            isinstance(refused, dict)
            and refused.get("ok") is False
            and refused.get("comment_ok") is None
            and edit_down.comments == [],
            f"refused={refused}",
        )
    )
    blind = _FakeGitHub("Plan\n- [ ] cache warm\n- [ ] index build\n")
    blind.fail_view = True
    subprocess.run = blind.route  # type: ignore[assignment]
    try:
        unread = update_group_pull_request(pub, 100020, members, url)
    finally:
        subprocess.run = saved
    results.append(
        (
            "an unreadable body posts no comment",
            isinstance(unread, dict)
            and unread.get("comment_ok") is None
            and unread.get("body_ok") is None
            and blind.comments == [],
            f"unread={unread}",
        )
    )
    healer = _FakeGitHub("Plan\n- [ ] cache warm\n- [ ] index build\n")
    healer.fail_edit = True
    subprocess.run = healer.route  # type: ignore[assignment]
    try:
        _write_receipt(
            pub,
            100030,
            state="awaiting_review",
            branch="b",
            group=members,
            pull_request=url,
            public_head="def5678abc",
        )
        healer.fail_edit = False
        healed = update_group_pull_request(pub, 100030, members, url)
    finally:
        subprocess.run = saved
    results.append(
        (
            "the next publish heals a missed tick and comments once",
            isinstance(healed, dict)
            and healed.get("ok") is True
            and "- [x] cache warm" in healer.body
            and "- [x] index build" in healer.body
            and len(healer.comments) == 1,
            f"healed={healed} comments={healer.comments}",
        )
    )


def _group_sequence_checks(base: Path) -> list[tuple[str, object, str]]:
    results: list[tuple[str, object, str]] = []
    _publish_sequence_checks(base, results)
    _failure_ordering_checks(base, results)
    return results


__all__ = ["_group_sequence_checks"]
