# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The gh re-export module: every published name resolves on it.

The module was split into ``gh_read``, ``gh_write``, ``gh_checks``, and
``gh_target``, and the split renamed ``_repo_target`` to ``repo_target`` while
``__all__`` kept naming neither it nor ``gh_create_draft_pull_request``. The
names were importable but unpublished, so a caller reading ``__all__`` as the
contract saw a short list and nothing failed. This test closes that hole.
"""

import argus.gitsync.gh as gh


def test_every_published_name_resolves() -> None:
    missing = [name for name in gh.__all__ if not hasattr(gh, name)]
    assert missing == [], f"gh.__all__ names that do not resolve: {missing}"


def test_the_split_names_are_published() -> None:
    # The two the split left out. Named here so a second drop fails loudly
    # rather than only shrinking the contract.
    assert "repo_target" in gh.__all__
    assert "gh_create_draft_pull_request" in gh.__all__


def test_no_published_name_is_listed_twice() -> None:
    assert len(gh.__all__) == len(set(gh.__all__))
