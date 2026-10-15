"""Tests for the prose one-paragraph-per-line gate (scripts/check_prose.py)."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_prose.py"


def run_checker(*files: Path) -> subprocess.CompletedProcess[str]:
    """Run the checker over fixture files and return the finished process."""
    return subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, str(SCRIPT), *(str(f) for f in files)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_prose_passes(tmp_path: Path) -> None:
    """Paragraphs, items, fences, and frontmatter each pass on their own."""
    doc = tmp_path / "clean.md"
    doc.write_text(
        "---\ntitle: x\n---\n\n# T\n\nOne line paragraph.\n\n"
        "- item one\n- item two\n\n```\n  indented code\n```\n",
        encoding="utf-8",
    )
    proc = run_checker(doc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "one paragraph per line" in proc.stdout


def test_wrapped_and_indented_prose_fails(tmp_path: Path) -> None:
    """A split paragraph and an indented continuation both fail."""
    doc = tmp_path / "wrapped.md"
    doc.write_text(
        "# T\n\nSplit paragraph\nacross two lines.\n\n- item\n  continuation\n",
        encoding="utf-8",
    )
    proc = run_checker(doc)
    assert proc.returncode == 1
    assert "wrapped paragraph continues here" in proc.stdout
    assert "indented continuation" in proc.stdout
