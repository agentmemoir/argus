"""Tests for the source-file line-limit gate (scripts/check_line_limit.py)."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_line_limit.py"


def run_checker(*files: Path) -> subprocess.CompletedProcess[str]:
    """Run the checker over fixture files and return the finished process."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(f) for f in files)],
        capture_output=True,
        text=True,
        check=False,
    )


def write_lines(path: Path, count: int) -> None:
    """Write count physical lines, each ending in a newline."""
    path.write_text("x\n" * count, encoding="utf-8")


def test_shell_file_at_the_limit_passes(tmp_path: Path) -> None:
    """A shell file exactly at 450 lines passes."""
    script = tmp_path / "ok.sh"
    write_lines(script, 450)
    proc = run_checker(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "within their line limits" in proc.stdout


def test_shell_file_over_450_fails(tmp_path: Path) -> None:
    """A shell file at 451 lines fails at 450."""
    script = tmp_path / "big.sh"
    write_lines(script, 451)
    proc = run_checker(script)
    assert proc.returncode == 1
    assert "over the 450-line limit" in proc.stdout


def test_python_file_over_350_fails(tmp_path: Path) -> None:
    """Python keeps the tighter 350-line limit, not 450."""
    module = tmp_path / "big.py"
    write_lines(module, 351)
    proc = run_checker(module)
    assert proc.returncode == 1
    assert "over the 350-line limit" in proc.stdout


def test_python_file_at_350_passes(tmp_path: Path) -> None:
    """A Python file exactly at 350 lines passes."""
    module = tmp_path / "ok.py"
    write_lines(module, 350)
    proc = run_checker(module)
    assert proc.returncode == 0, proc.stdout + proc.stderr
