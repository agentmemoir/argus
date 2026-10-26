#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print the state and error glide appends to a phase's session log.

glide writes its pipeline plan and then one JSON object holding the run's
state and any error. The text before it is a plan, not JSON, so a whole-file
parse fails by design. The object is found by trying each `{` from the end of
the text and keeping the one whose parse reaches the end, which tolerates
braces inside the error text.

The answer is three lines with fixed prefixes, `state=`, `error=` and `hook=`
(the failing hook point, such as `pre_run`), so one
invocation reads the payload once. Each value is folded onto its line here, so
a value can never add a line.

An optional second argument is a byte offset: only text from that offset on is
considered. The session log is appended to across launches, so the driver
passes the log's length at launch and this module ignores any payload that came
before it.

At most `WINDOW_BYTES` are read. The window starts at the offset, or at the
last `WINDOW_BYTES` of the file when that is later, so a long log costs a fixed
amount. One megabyte is safe because glide appends one small JSON object after
a short plan: the object always ends the file, inside the window.

A file with no such object, or an object whose `state` is not a string, prints
nothing and exits non-zero, so the driver can keep its previous answer. A
missing or unreadable file, an empty or non-numeric offset, a decode failure,
and too-deep nesting fail the same way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

#: The most bytes one read may take. See the module docstring for why it holds.
WINDOW_BYTES = 1024 * 1024


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


def read_window(path: Path, offset: int) -> bytes:
    """At most `WINDOW_BYTES` from `offset`, or the file's tail if later."""
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        start = max(offset, size - WINDOW_BYTES)
        handle.seek(start)
        data = handle.read(WINDOW_BYTES)
    if start == offset:
        # An offset is a launch boundary, so it is always a character boundary.
        return data
    # A tail window can start inside a character. Skip its continuation bytes,
    # so a clean log still decodes: 10xxxxxx is the continuation form.
    skipped = 0
    while skipped < 3 and skipped < len(data) and data[skipped] & 0xC0 == 0x80:
        skipped += 1
    return data[skipped:]


def one_line(value: str) -> str:
    """The value folded onto one line, as the shell used to fold it."""
    folded = value.replace("\r", "").replace("\n", "\\n")
    return "".join(" " if ch < " " or ch == "\x7f" else ch for ch in folded)


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        return 2
    try:
        offset = int(argv[1]) if len(argv) == 2 else 0
        data = read_window(Path(argv[0]), max(offset, 0))
    except (OSError, ValueError):
        return 1
    if offset < 0:
        return 2
    try:
        payload = trailing_object(data.decode("utf-8"))
    except (UnicodeDecodeError, RecursionError):
        return 1
    if payload is None:
        return 1
    state = payload.get("state")
    if not isinstance(state, str):
        return 1
    error = payload.get("error")
    print(f"state={one_line(state)}")
    print(f"error={one_line(error) if isinstance(error, str) else ''}")
    hook = payload.get("hook_point")
    print(f"hook={one_line(hook) if isinstance(hook, str) else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
