# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A routed ``gh`` stand-in and the pull request builders the group gate checks share."""

import json
import types
from pathlib import Path
from typing import Any

from argus.gitsync.selftest_fixture import _StubGH

URL = "https://github.com/example-org/example-repo/pull/7"
REF = {"owner": "example-org", "repo": "example-repo", "host": "github.com"}
BRANCH = "feature/group-cache-warm"
MEMBERS = ["100020", "100030"]
HEAD = "a" * 40


class _RoutedGH(_StubGH):
    """A ``gh`` stand-in that answers each subcommand on its own.

    A route is keyed by the words after ``gh`` (``pr list``), or by three words
    (``pr checks 7``) to answer one pull request differently. Its value is a
    ``subprocess`` result as a dict. A subcommand with no route fails, so a call
    the code was not expected to make shows up as a refusal and not a pass.
    """

    def __init__(self, routes: dict[str, dict[str, Any]]) -> None:
        super().__init__({})
        self.routes = routes

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
        if not argv or Path(argv[0]).name != "gh":
            return self.saved(argv, **kwargs)
        self.calls.append(list(argv))
        answer = self.routes.get(" ".join(argv[1:4])) or self.routes.get(
            " ".join(argv[1:3]), {"returncode": 1}
        )
        return types.SimpleNamespace(
            returncode=answer.get("returncode", 0),
            stdout=answer.get("stdout", ""),
            stderr="",
        )

    def __enter__(self) -> "_RoutedGH":
        super().__enter__()
        return self

    def ran(self, subcommand: str) -> list[list[str]]:
        return [c for c in self.calls if " ".join(c[1:3]) == subcommand]


def _listing(*prs: dict[str, Any]) -> dict[str, Any]:
    return {"stdout": json.dumps(list(prs))}


def _rows(*buckets: str) -> list[dict[str, Any]]:
    run = "https://github.com/example-org/example-repo/actions/runs/4242"
    return [
        {"name": f"job{i}", "bucket": b, "link": f"{run}/job/{i}"}
        for i, b in enumerate(buckets, 1)
    ]


def _pr(number: int, **fields: Any) -> dict[str, Any]:
    return {
        "number": number,
        "isDraft": False,
        "isCrossRepository": False,
        "mergeable": "MERGEABLE",
        "reviewDecision": "",
        "headRefOid": HEAD,
        **fields,
    }


def _checks(*buckets: str) -> dict[str, Any]:
    return {"stdout": json.dumps(_rows(*buckets))}


__all__ = [
    "BRANCH",
    "HEAD",
    "MEMBERS",
    "REF",
    "URL",
    "_RoutedGH",
    "_checks",
    "_listing",
    "_pr",
    "_rows",
]
