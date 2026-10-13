# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Which pull request comments may show their text, from the issue trust settings.

The ``issues.*`` keys already decide which issue comments an agent may read:
allowlisted users and apps, trusted associations, and the CODEOWNERS file. A
pull request comment uses the same check, so one set of settings governs both.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from argus import conventions
from argus.issues.client import IssueClient
from argus.issues.trust import Trust

Row = dict[str, Any]


class Allows(Protocol):
    def allows(self, item: Row, *, comment: bool = False) -> bool: ...


@dataclass(frozen=True)
class Visibility:
    """Which comments show their text, and whether untrusted text is shown as data."""

    trusted: Callable[[Row], bool]
    show_untrusted: bool

    @staticmethod
    def closed() -> "Visibility":
        """Trust nothing: no comment text is shown."""
        return Visibility(trusted=lambda _row: False, show_untrusted=False)


def adapt(checker: Allows) -> Callable[[Row], bool]:
    """Map one REST review comment onto the item shape the issue trust check reads."""

    def check(row: Row) -> bool:
        user: Row = row.get("user") or {}
        item: Row = {
            "authorAssociation": row.get("author_association"),
            "author": {"login": user.get("login"), "type": user.get("type")},
        }
        return checker.allows(item, comment=True)

    return check


def build_visibility(root: Path) -> Visibility:
    """Visibility from the configured ``issues.*`` settings.

    Raises ``IssuesUnavailable`` when those settings cannot be read, so the
    caller reads nothing rather than guessing who is trusted.
    """
    trust = Trust(root, IssueClient())
    show = conventions.setting("issues.untrusted-comments", "ignore") == "data-only"
    return Visibility(trusted=adapt(trust), show_untrusted=show)
