# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Render a loaded configuration as shell, report, or JSON text."""

import shlex
from pathlib import Path

from argus.config.schema import _REPORT_KEY, SCHEMA, SHELL_VARS


def _shell_value(value):
    if isinstance(value, list):
        if not value:
            return "()"
        return "( " + " ".join(shlex.quote(str(item)) for item in value) + " )"
    if isinstance(value, bool):
        return "1" if value else "0"
    return shlex.quote(str(value))


def shell_lines(config):
    """One assignment per present variable, plus its source, in schema order."""
    lines = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        var = SHELL_VARS[key]
        lines.append(f"{var}={_shell_value(config.values[key])}")
        lines.append(f"ARGUS_SOURCE_{var}='{config.sources[key]}'")
    return lines


def _render(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def report_lines(config):
    """`key<TAB>source<TAB>value` for every present key, in schema order."""
    lines = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        lines.append(f"{key}\t{config.sources[key]}\t{_render(config.values[key])}")
    return lines


def _json_view(config):
    values = {}
    for key, value in config.values.items():
        values[key] = str(value) if isinstance(value, Path) else value
    return {"path": str(config.path), "values": values, "sources": dict(config.sources)}
