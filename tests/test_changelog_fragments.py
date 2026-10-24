"""Tests for the changelog fragments (scripts/changelog.py).

Two fragments added on two branches merge with no conflict. `build` folds the
fragments in category order. `check` rejects a bad name, an empty fragment, and
a paragraph spread over several lines.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "changelog.py"
CHANGELOG_TEXT = "# Changelog\n\nIntro.\n\n## [Unreleased]\n\nPointer line.\n"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, no shell
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def fragment(root: Path, name: str, text: str) -> None:
    directory = root / "changelog.d"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(text + "\n", encoding="utf-8")


def test_fragments_from_two_branches_merge_without_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "CHANGELOG.md").write_text(CHANGELOG_TEXT, encoding="utf-8")
    git(repo, "add", "CHANGELOG.md")
    git(repo, "commit", "-q", "-m", "base")
    for branch, name in (("one", "first.fixed.md"), ("two", "second.added.md")):
        git(repo, "checkout", "-q", "-b", branch, "main")
        fragment(repo, name, f"Entry from {branch}.")
        git(repo, "add", "changelog.d")
        assert git(repo, "commit", "-q", "-m", branch).returncode == 0
    git(repo, "checkout", "-q", "main")
    assert git(repo, "merge", "-q", "--no-edit", "one").returncode == 0
    merged = git(repo, "merge", "-q", "--no-edit", "two")
    assert merged.returncode == 0, merged.stderr
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG_TEXT
    assert run(repo, "check").returncode == 0


def test_build_folds_fragments_in_category_order(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_TEXT, encoding="utf-8")
    fragment(tmp_path, "zeta-fix.fixed.md", "Fixed entry.")
    fragment(tmp_path, "alpha-add.added.md", "Added entry.")
    fragment(tmp_path, "mid-change.changed.md", "Changed entry.")
    result = run(tmp_path, "build", "1.0.0")
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = [text.index(f"### {name}") for name in ("Added", "Changed", "Fixed")]
    assert headings == sorted(headings)
    assert text.index("- Added entry.") < text.index("- Fixed entry.")
    assert re.search(r"## \[1\.0\.0\] - \d{4}-\d{2}-\d{2}", text)
    assert text.index("## [Unreleased]") < text.index("## [1.0.0]")
    assert "Pointer line." in text
    assert not (tmp_path / "changelog.d" / "zeta-fix.fixed.md").exists()
    assert not (tmp_path / "changelog.d" / "alpha-add.added.md").exists()


def test_check_accepts_a_good_fragment(tmp_path: Path) -> None:
    fragment(tmp_path, "good-slug.security.md", "One line, one paragraph.")
    result = run(tmp_path, "check")
    assert result.returncode == 0, result.stderr
    assert "1 ok" in result.stdout


@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("Bad-Name.fixed.md", "Entry.", "name must be"),
        ("slug.md", "Entry.", "name must be"),
        ("-leading.fixed.md", "Entry.", "name must be"),
        ("slug.fixed.txt", "Entry.", "name must be"),
        ("slug.bugfix.md", "Entry.", "category must be"),
        ("empty.fixed.md", "   ", "empty fragment"),
        ("wrapped.fixed.md", "First half of it\nsecond half.", "spans 2 lines"),
    ],
)
def test_check_rejects_a_bad_fragment(
    tmp_path: Path, name: str, text: str, reason: str
) -> None:
    fragment(tmp_path, name, text)
    result = run(tmp_path, "check")
    assert result.returncode == 1
    assert name in result.stderr
    assert reason in result.stderr
