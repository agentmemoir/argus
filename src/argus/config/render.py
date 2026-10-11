# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Render a loaded configuration as shell, report, or JSON text."""

import json
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, cast

from argus.config.schema import _REPORT_KEY, SCHEMA, SHELL_VARS

if TYPE_CHECKING:
    from argus.config.loader import Config


def _shell_value(value: object) -> str:
    if isinstance(value, dict):
        return shlex.quote(json.dumps(value, separators=(",", ":")))
    if isinstance(value, list):
        items = cast("list[object]", value)
        if not items:
            return "()"
        return "( " + " ".join(shlex.quote(str(item)) for item in items) + " )"
    if isinstance(value, bool):
        return "1" if value else "0"
    return shlex.quote(str(value))


def shell_lines(config: "Config") -> list[str]:
    """One assignment per present variable, plus its source, in schema order."""
    lines: list[str] = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        var = SHELL_VARS[key]
        lines.append(f"{var}={_shell_value(config.values[key])}")
        lines.append(f"ARGUS_SOURCE_{var}='{config.sources[key]}'")
    return lines


def _render(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return " ".join(str(item) for item in cast("list[object]", value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def report_lines(config: "Config") -> list[str]:
    """`key<TAB>source<TAB>value` for every present key, in schema order."""
    lines: list[str] = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        lines.append(f"{key}\t{config.sources[key]}\t{_render(config.values[key])}")
    return lines


def _json_view(config: "Config") -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in config.values.items():
        values[key] = str(value) if isinstance(value, Path) else value
    return {
        "path": str(config.path),
        "values": values,
        "sources": dict(config.sources),
    }


__all__ = ["_json_view", "_render", "_shell_value"]
