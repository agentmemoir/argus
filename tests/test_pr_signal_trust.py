# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Pull request comment trust: the adapter onto the issue trust check, and the closed default."""

from pathlib import Path
from typing import Any

from argus import conventions
from argus.gitsync.pr_signal_trust import Visibility, adapt, build_visibility


class _Stub:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], bool]] = []

    def allows(self, item: dict[str, Any], *, comment: bool = False) -> bool:
        self.calls.append((item, comment))
        return item["author"]["login"] == "member"


def test_adapt_maps_a_review_comment_onto_the_issue_trust_item() -> None:
    stub = _Stub()
    row = {"author_association": "MEMBER", "user": {"login": "member", "type": "User"}}
    assert adapt(stub)(row) is True
    item, comment = stub.calls[0]
    assert comment is True
    assert item == {
        "authorAssociation": "MEMBER",
        "author": {"login": "member", "type": "User"},
    }


def test_closed_visibility_trusts_nothing_and_shows_no_untrusted_text() -> None:
    row = {"author_association": "OWNER", "user": {"login": "owner", "type": "User"}}
    assert Visibility.closed().trusted(row) is False
    assert Visibility.closed().show_untrusted is False


def test_build_visibility_reads_trust_without_an_issue_source(tmp_path: Path) -> None:
    previous = conventions.override(
        {"issues.trusted-authors": ["reviewer"], "issues.resolve-teams": False}
    )
    try:
        visibility = build_visibility(tmp_path)
        member = {"author_association": "NONE", "user": {"login": "reviewer"}}
        outsider = {"author_association": "NONE", "user": {"login": "outsider"}}
        assert visibility.trusted(member) is True
        assert visibility.trusted(outsider) is False
    finally:
        conventions.configure(previous)
