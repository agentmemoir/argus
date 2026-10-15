"""Fail a source file that exceeds its line limit.

The line-limit rule: a source file stays small enough to review. Python keeps
the repository's tighter 350-line ceiling; every other scanned language uses
450. Markdown is not scanned. The checker reads tracked plus untracked files
via git, so a new file fails before it is staged.
"""

import subprocess
import sys
from pathlib import Path

PYTHON_LIMIT = 350
DEFAULT_LIMIT = 450
EXTENSIONS = (
    ".rs",
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".java",
    ".kt",
    ".scala",
    ".c",
    ".h",
    ".cc",
    ".hh",
    ".cpp",
    ".hpp",
    ".cxx",
    ".hxx",
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
)


def limit_for(name: str) -> int:
    """Return the line limit for a file, by its extension."""
    if Path(name).suffix == ".py":
        return PYTHON_LIMIT
    return DEFAULT_LIMIT


def line_count(text: str) -> int:
    """Count physical lines the way an editor numbers them."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def check(paths: list[str]) -> list[str]:
    """Return one message per file over its limit."""
    hits: list[str] = []
    for raw in paths:
        try:
            text = Path(raw).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            hits.append(f"{raw}: cannot read file: {exc}")
            continue
        limit = limit_for(raw)
        count = line_count(text)
        if count > limit:
            hits.append(f"ERROR: {raw} has {count} lines, over the {limit}-line limit")
    return hits


def git_files() -> list[str]:
    """List tracked plus untracked scanned source files via git."""
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files failed: {proc.stderr.strip()}")
    return [
        line for line in proc.stdout.splitlines() if Path(line).suffix in EXTENSIONS
    ]


def main(argv: list[str]) -> int:
    """Check the given files, defaulting to git-listed sources. Return 0/1."""
    paths = argv[1:] or git_files()
    hits = check(paths)
    if hits:
        print("\n".join(hits))
        print(f"{len(hits)} file(s) over the line limit. Split before adding.")
        return 1
    print(f"{len(paths)} file(s) within their line limits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
