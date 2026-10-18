#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Print one field of the result glide appends to a phase's session log.

glide writes its pipeline plan and then one JSON object holding the run's
state and any error. The text before it is a plan, not JSON, so a whole-file
parse fails by design. The object is found by trying each `{` from the end of
the file and keeping the one whose parse reaches the end, which tolerates
braces inside the error text.

A file with no such object, or an object without the requested string field,
prints nothing and exits non-zero, so the driver can keep its previous answer.

An optional third argument is a byte offset: only text from that offset on is
parsed. The session log is appended to across launches, so the driver passes
the log's length at launch and this module ignores any payload that came
before it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast


def trailing_object(text: str) -> dict[str, object] | None:
    """The JSON object that ends `text`, or None when there is not one."""
    end = len(text.rstrip())
    if end == 0 or text[end - 1] != "}":
        return None
    decoder = json.JSONDecoder()
    for index in range(end - 1, -1, -1):
        if text[index] != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text, index)
        except ValueError:
            continue
        if consumed == end and isinstance(value, dict):
            return cast("dict[str, object]", value)
    return None


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        return 2
    try:
        offset = int(argv[2]) if len(argv) == 3 else 0
        data = Path(argv[0]).read_bytes()
    except (OSError, ValueError):
        return 1
    if offset < 0:
        return 2
    try:
        text = data[offset:].decode("utf-8")
        payload = trailing_object(text)
    except (UnicodeDecodeError, RecursionError):
        return 1
    if payload is None:
        return 1
    value = payload.get(argv[1])
    if not isinstance(value, str):
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
