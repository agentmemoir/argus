"""Fail on hard-wrapped prose: one paragraph per line.

The prose rule (agent-rules/line-breaks.md): one paragraph per line, so the
reader's viewer does the wrapping. A new line is only for a new paragraph, a
list item, a heading, or a code block. This script reports indented
continuation lines and consecutive prose lines outside fenced code blocks,
HTML comments, and YAML frontmatter. It scans tracked plus untracked
markdown files, so LICENSE (not markdown) is never scanned.
"""

import re
import subprocess
import sys
from pathlib import Path

HEADING = re.compile(r"#{1,6}\s")
ITEM = re.compile(r"\s*([-*+>]|\d+[.)])\s")
HR = re.compile(r"(---|___|\*\*\*)\s*$")
SETEXT = re.compile(r"=+\s*$")
LINKDEF = re.compile(r"\[[^\]]*\]:")
HTML_LINE = re.compile(r"<[^>]*>$")


def is_structural(line: str) -> bool:
    """Return True when a line may start a new block (never a wrap)."""
    if line.startswith("|"):
        return True
    return (
        HEADING.match(line) is not None
        or ITEM.match(line) is not None
        or HR.match(line) is not None
        or SETEXT.match(line) is not None
        or LINKDEF.match(line) is not None
        or HTML_LINE.match(line) is not None
    )


def find_violations(text: str, name: str) -> list[str]:
    """Report hard-wrapped lines in text as name:line: message entries."""
    hits: list[str] = []
    fence = False
    comment = False
    front = False
    prev = ""
    for lineno, line in enumerate(text.splitlines(), 1):
        if lineno == 1 and line == "---":
            front = True
            continue
        if front and line == "---":
            front = False
            prev = ""
            continue
        if front:
            continue
        if "<!--" in line and "-->" not in line:
            comment = True
            prev = ""
            continue
        if comment and "-->" in line:
            comment = False
            prev = ""
            continue
        if comment:
            continue
        if line.startswith("```") or line.startswith("~~~"):
            fence = not fence
            prev = ""
            continue
        if fence:
            continue
        if not line.strip():
            prev = ""
            continue
        if line[0] in (" ", "\t") and not is_structural(line):
            hits.append(f"{name}:{lineno}: indented continuation")
            prev = ""
        elif is_structural(line):
            prev = ""
        elif prev:
            hits.append(f"{name}:{lineno}: wrapped paragraph continues here")
            prev = ""
        else:
            prev = line
    return hits


def git_markdown_files() -> list[str]:
    """List tracked plus untracked markdown files via git."""
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        ["git", "ls-files", "*.md", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    """Check the given files, defaulting to git-listed markdown. Return 0/1."""
    paths = argv[1:] or git_markdown_files()
    hits: list[str] = []
    for raw in paths:
        try:
            text = Path(raw).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            hits.append(f"{raw}:0: cannot read file: {exc}")
            continue
        hits.extend(find_violations(text, raw))
    if hits:
        print("\n".join(hits))
        print(
            f"{len(hits)} hard-wrapped prose line(s) found. "
            "Rejoin each paragraph to one line."
        )
        return 1
    print("prose is one paragraph per line")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
