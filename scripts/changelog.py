"""Changelog fragments: check them, and fold them into CHANGELOG.md at release.

A change adds one fragment under changelog.d/, named <slug>.<category>.md,
holding one-line paragraphs. `check` fails on a fragment with a bad name, an
empty file, or a paragraph spread over several lines. `build <version>` writes
the fragments into CHANGELOG.md under a version heading, grouped by category,
and deletes them.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = "changelog.d"
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
# Keep a Changelog order: the order the headings appear in a release.
CATEGORIES = ("added", "changed", "deprecated", "removed", "fixed", "security")
NAME = re.compile(r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?P<category>[a-z]+)\.md")


def fragment_paths(root: Path) -> list[Path]:
    directory = root / DIRECTORY
    if not directory.is_dir():
        return []
    return sorted(directory.iterdir())


def paragraphs(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def problems(path: Path) -> list[str]:
    match = NAME.fullmatch(path.name)
    if match is None:
        return [
            f"{path.name}: name must be <slug>.<category>.md, slug in lowercase words joined by hyphens"
        ]
    if match["category"] not in CATEGORIES:
        return [f"{path.name}: category must be one of {', '.join(CATEGORIES)}"]
    if not path.is_file():
        return [f"{path.name}: not a regular file"]
    try:
        blocks = paragraphs(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return [f"{path.name}: not UTF-8 text"]
    if not blocks:
        return [f"{path.name}: empty fragment; write one paragraph or delete the file"]
    return [
        f"{path.name}: paragraph {n} spans {len(block)} lines; write it on one line"
        for n, block in enumerate(blocks, start=1)
        if len(block) > 1
    ]


def check(root: Path) -> list[str]:
    return [error for path in fragment_paths(root) for error in problems(path)]


def build(root: Path, version: str, today: date) -> str:
    """Fold the fragments into CHANGELOG.md; return an error text, or ''."""
    errors = check(root)
    fragments = fragment_paths(root)
    if errors:
        return "\n".join(errors)
    if not fragments:
        return "no fragments in changelog.d; nothing to build"
    text = (root / CHANGELOG).read_text(encoding="utf-8")
    if f"## [{version}]" in text:
        return f"CHANGELOG.md already has a section for {version}"
    grouped: dict[str, list[str]] = {name: [] for name in CATEGORIES}
    for path in fragments:
        category = path.name.split(".")[1]  # the slug has no dots; check passed
        for block in paragraphs(path.read_text(encoding="utf-8")):
            grouped[category].append("- " + " ".join(block))
    parts = [f"## [{version}] - {today.isoformat()}"]
    for name in CATEGORIES:
        if grouped[name]:
            parts.append(f"### {name.capitalize()}\n\n" + "\n".join(grouped[name]))
    release = "\n\n".join(parts)
    lines = text.split("\n")
    start = lines.index(UNRELEASED)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    head = "\n".join(lines[:end]).rstrip("\n")
    tail = "\n".join(lines[end:])
    (root / CHANGELOG).write_text(
        f"{head}\n\n{release}\n\n{tail}".rstrip("\n") + "\n", encoding="utf-8"
    )
    for path in fragments:
        path.unlink()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="fail on a bad changelog fragment")
    release = commands.add_parser("build", help="fold fragments into CHANGELOG.md")
    release.add_argument("version", help="the version heading to write")
    args = parser.parse_args(argv)
    if args.command == "check":
        errors = check(args.root)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"changelog fragments: {len(fragment_paths(args.root))} ok")
        return 0
    error = build(args.root, args.version, date.today())
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"CHANGELOG.md: section {args.version} written; fragments removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
