# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shape checks for the values GitHub returns, used by the issue client."""

from typing import Any, cast

from argus.issues.transport import IssuesUnavailable


def issue_number(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 9999999999
    ):
        raise IssuesUnavailable("invalid_response", "issue number")
    return value


def objects(value: Any, operation: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise IssuesUnavailable("invalid_response", operation)
    items = cast("list[object]", value)
    if any(not isinstance(item, dict) for item in items):
        raise IssuesUnavailable("invalid_response", operation)
    return cast("list[dict[str, Any]]", items)


def mapping(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IssuesUnavailable("invalid_response", operation)
    return cast("dict[str, Any]", value)
