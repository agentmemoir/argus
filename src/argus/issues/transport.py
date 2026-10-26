# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bounded gh process boundary; remote text never enters errors."""

import json
import os
import signal
import subprocess
from typing import Any

from argus.select.work_source import WorkSourceError


class IssuesUnavailable(WorkSourceError):
    """Issue selection failed closed, with a safe diagnostic category."""

    def __init__(self, reason: str, operation: str) -> None:
        self.reason = reason
        super().__init__(f"issues {reason}: {operation}")


def run(binary: str, argv: list[str], timeout: float, *, parse: bool = True) -> Any:
    operation = " ".join(argv[:2])
    try:
        proc = subprocess.Popen(
            [binary, *argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        raise IssuesUnavailable("gh_missing", operation) from exc
    try:
        raw, error = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise IssuesUnavailable("timeout", operation) from exc
    if proc.returncode:
        lower = error.lower()
        if proc.returncode == 4 or any(
            s in lower for s in ("authentication", "bad credentials", "gh auth login")
        ):
            reason = "token"
        elif "rate limit" in lower or "rate-limit" in lower:
            reason = "rate_limit"
        elif any(
            s in lower
            for s in (
                "connect",
                "network",
                "resolve host",
                "no such host",
                "tls handshake",
            )
        ):
            reason = "network"
        else:
            reason = "command_failed"
        raise IssuesUnavailable(reason, operation)
    if not parse:
        return None
    if not raw.strip():
        raise IssuesUnavailable("empty_response", operation)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise IssuesUnavailable("invalid_json", operation) from exc
