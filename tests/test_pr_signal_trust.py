# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Pull request comment trust: the adapter onto the issue trust check, and the closed default."""

from typing import Any

from argus.gitsync.pr_signal_trust import Visibility, adapt


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
