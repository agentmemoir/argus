# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read JSONC documents with the shared comment scanner."""

import json
from pathlib import Path
from typing import Any, cast

from argus.config.errors import ConfigError


def strip_comments(text: str) -> str:
    """Remove `//` and `/* */` comments, keeping string literals intact.

    The scanner follows RFC 8259, section 7 for escapes: inside a string a
    reverse solidus escapes the character after it, so an escaped quote does
    not close the string. Newlines inside a block comment are kept so a parse
    error still points at roughly the original line.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            out.append(char)
            index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            index += 2
            while index < length and text[index] != "\n":
                index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            closed = False
            while index < length:
                if text[index] == "\n":
                    out.append("\n")
                    index += 1
                elif (
                    text[index] == "*" and index + 1 < length and text[index + 1] == "/"
                ):
                    index += 2
                    closed = True
                    break
                else:
                    index += 1
            if not closed:
                raise ConfigError("unterminated block comment")
        else:
            out.append(char)
            index += 1
    return "".join(out)


def read_document(path: Path) -> dict[str, Any]:
    """Read a JSONC object, reporting its file and parse location on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read config file '{path}': {exc}") from exc
    try:
        cleaned = strip_comments(text)
    except ConfigError as exc:
        raise ConfigError(f"cannot parse config file '{path}': {exc}") from exc
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"cannot parse config file '{path}' at line {exc.lineno} "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file '{path}' must hold a JSON object")
    return cast("dict[str, Any]", raw)
