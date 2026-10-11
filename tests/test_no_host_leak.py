# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The public repository must contain nothing from a host's private tree.

Argus is developed alongside a private host project, and this repository is
world-readable and permanent. Every commit is a publication, so a leak is not a
bug to fix later -- it is already out.

The rules this file enforces come from the repository's own contribution
notes: no host paths, no machine names, no secrets, and no run state. The scan
is structural on purpose: spelling a host's own directory or project name
inside a published scanner would itself leak the layout this file exists to
protect, so each pattern matches a shape instead, and the host checks its own
literals from its own checkout, where they belong.

The scan runs as a test rather than only as a pre-commit step, because a gate
nobody runs is not a gate. ``make leakscan`` runs the same suite. The file set
is what git would publish -- tracked files plus untracked files that git does
not ignore -- so a machine-local file can never fail the gate.

Home paths, account ids, and notification literals are owned by
``tests/test_identifiers.py``; this file owns host-checkout paths, credential
assignments, and run state.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path

#: The repository root, from this file: tests/ -> the root.
ROOT = Path(__file__).resolve().parents[1]

#: Directories that are not ours to scan. ``.venv`` is installed dependencies,
#: and the cache directories are written by tools during a run.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "private",
        "__pycache__",
        ".pytest_cache",
        ".pytest-tmp",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
    }
)

#: Files whose content legitimately holds a pattern, because the file exists to
#: describe the rule. Skipping them would be cheating if the rule were not also
#: asserted below; it is, in ``test_each_allowlisted_file_still_describes_its_rule``.
ALLOWLIST = frozenset(
    {
        Path("tests/test_no_host_leak.py"),
        Path("tests/test_leak_patterns.py"),
    }
)

#: The literal markers each allowlisted file must still carry. A file on the
#: allowlist is there because it describes a pattern; if it stopped describing
#: it, the scan would be looking for something nobody had written down.
ALLOWLIST_MARKERS = {
    Path("tests/test_no_host_leak.py"): ("private/", "token", "run.json"),
    Path("tests/test_leak_patterns.py"): ("private/", "token"),
}

#: A nested private-checkout path: ``private/`` followed by one path segment.
#: This is deliberately structural and never spells a host's real name.
HOST_PATH = re.compile(r"(?<![A-Za-z0-9_.-])private/[A-Za-z0-9_.-]+")

#: A credential assignment. The right-hand side must be a *literal*: a quoted
#: string, or a bare opaque value that ends at the assignment. A call or a
#: dotted attribute is an expression, not a credential, and an assignment
#: quoted as a whole is a mention, not an assignment; all three are excluded.
#: That is what keeps ``token = match.group(1)`` and a redaction fixture such
#: as ``reason="token=..."`` out of the results: a signal token is not a
#: credential, and a scan that flagged one is a scan people learn to skip.
SECRET = re.compile(
    r"\b(api[_-]?key|token|secret|password|passwd|credential)"
    r"\s*[:=]\s*(?:"
    r"[\"'][^\"'\s]{6,}[\"']"
    r'|[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_.("\'-])'
    r")",
    re.IGNORECASE,
)

#: A phase run-directory artefact. One file with this name is run state, and
#: run state is never committed.
RUN_STATE_NAMES = (
    "run.json",
    "ledger.json",
    "publication.json",
    "reservation.json",
)


def tracked_files() -> list[Path]:
    """Return every file the scan may read.

    The set is what git would publish: tracked files plus untracked files that
    git does not ignore. A machine-local file git ignores -- agent skills, tool
    caches -- is invisible to the scan even when present, so the gate means the
    same thing on every machine. An uncommitted source file still scans, so a
    leak fails before it is ever committed.

    Returns:
        Absolute paths, with the skip directories and the allowlist removed.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return _walk_tree()
    found: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        path = ROOT / os.fsdecode(raw)
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if set(relative.parts) & SKIP_DIRS:
            continue
        if relative in ALLOWLIST:
            continue
        found.append(path)
    return found


def _walk_tree() -> list[Path]:
    """Fall back to the working tree when git is unavailable.

    This keeps the scan running outside a checkout, with the known weakness
    that git-ignored files are read too. Inside a checkout the git-backed set
    above always wins.

    Returns:
        Absolute paths, with the skip directories and the allowlist removed.
    """
    found: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if set(relative.parts) & SKIP_DIRS:
            continue
        if relative in ALLOWLIST:
            continue
        found.append(path)
    return found


def rel(path: Path) -> str:
    """Return a path relative to the repository root, for a failure message."""
    return str(path.relative_to(ROOT))


def _lines(path: Path) -> list[str]:
    """Return a file's lines, skipping any that cannot be decoded."""
    try:
        return path.read_text(errors="strict").splitlines()
    except (UnicodeDecodeError, OSError):
        return []


class TestNoHostLeak:
    """Nothing from a host's private tree may appear here."""

    def test_the_scanner_actually_reads_files(self) -> None:
        """A scan that matched nothing because it read nothing would pass forever."""
        files = tracked_files()
        assert len(files) > 20
        assert any(rel(path).endswith(".py") for path in files)
        assert any(rel(path).endswith(".md") for path in files)

    def test_no_host_path(self) -> None:
        """No nested private-checkout path may be committed."""
        hits = [
            f"{rel(path)}: {line.strip()}"
            for path in tracked_files()
            for line in _lines(path)
            if HOST_PATH.search(line)
        ]
        assert not hits, "host path found:\n" + "\n".join(hits[:10])

    def test_gitignored_local_files_are_not_scanned(self) -> None:
        """A machine-local file is invisible to the scan, even when present.

        The skills tooling writes a lock file and a skills tree into the
        checkout, and a machine keeps them out of git. The scan answers what
        git would publish, so those files must not be read. Only what this test
        created is removed; a pre-existing skills tree is left alone.
        """
        probe = ROOT / ".agents" / "skills" / "probe" / "SKILL.md"
        probe.parent.mkdir(parents=True, exist_ok=True)
        try:
            probe.write_text("---\nnotes: private/research/probe.md\n---\n")
            assert probe not in tracked_files()
            hits = [
                line.strip()
                for path in tracked_files()
                for line in _lines(path)
                if "private/research/probe" in line
            ]
            assert not hits, "ignored probe file scanned:\n" + "\n".join(hits[:5])
        finally:
            with contextlib.suppress(OSError):
                probe.unlink()
                probe.parent.rmdir()

    def test_no_credential_assignment(self) -> None:
        """A bare ``token =`` is already a finding.

        The value is not required to look like a secret, because a placeholder
        in a fixture is exactly how one gets committed by accident.
        """
        hits = [
            f"{rel(path)}: {line.strip()}"
            for path in tracked_files()
            for line in _lines(path)
            if SECRET.search(line)
        ]
        assert not hits, "credential assignment found:\n" + "\n".join(hits[:10])

    def test_no_run_state(self) -> None:
        """A phase run-directory artefact is run state, never committed."""
        offenders = [
            rel(path) for path in tracked_files() if path.name in RUN_STATE_NAMES
        ]
        assert not offenders, f"run state committed: {offenders}"

    def test_no_run_directory(self) -> None:
        """A whole run directory is state too, whatever it is called."""
        offenders = [
            rel(path)
            for path in tracked_files()
            if "runs" in path.relative_to(ROOT).parts
        ]
        assert not offenders, f"run directory committed: {offenders}"

    def test_each_allowlisted_file_still_describes_its_rule(self) -> None:
        """Allowlisting a file must not allowlist a rule going missing."""
        for path, markers in ALLOWLIST_MARKERS.items():
            assert path.is_file(), f"{path} is allowlisted but not present"
            text = (ROOT / path).read_text()
            for marker in markers:
                assert marker in text, f"{path} no longer declares {marker!r}"
