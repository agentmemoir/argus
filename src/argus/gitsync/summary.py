# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-line sync summary for a gitsync result list."""

from typing import Any


def summarize(results: list[dict[str, Any]], mode: str) -> str:
    parts: list[str] = []
    for r in results:
        if r.get("ok"):
            first = str(r["warning"]).splitlines()[0] if r.get("warning") else ""
            note = f" ({first})" if first else ""
            parts.append(f"{r['repo']}: {r.get('action', 'ok')}{note}")
        else:
            parts.append(f"{r['repo']}: BLOCKED ({'; '.join(r.get('blockers', []))})")
    if not all(r.get("ok") for r in results):
        state = "BLOCKED"
    elif any(r.get("warning") for r in results):
        state = "DEGRADED"
    else:
        state = "ok"
    return f"{mode} sync {state}: " + "; ".join(parts)
