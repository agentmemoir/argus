"""`make self-test` and `make guard-faults` refuse to start outside CI.

Every run passes RUN=echo, so the suite commands are printed and never run,
even when the guard is broken.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKE = shutil.which("make") or "make"
# The first suite command each target runs once the guard lets it through.
TARGETS = [
    ("self-test", "python scripts/driver_cases.py"),
    ("guard-faults", "python tests/guard_faults.py"),
]
CLEARED = ("CI", "ARGUS_ALLOW_LOCAL", "MAKEFLAGS", "MAKELEVEL")


def run_make(target: str, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one target with RUN=echo, CI and the override cleared, then `extra`."""
    env = {k: v for k, v in os.environ.items() if k not in CLEARED}
    env.update(extra)
    return subprocess.run(  # noqa: S603 - argv list, no shell
        [MAKE, "-s", target, "RUN=echo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(("target", "first_command"), TARGETS)
def test_refuses_outside_ci(target: str, first_command: str) -> None:
    """Without CI or the override, the target fails before any suite command."""
    proc = run_make(target, {})
    assert proc.returncode != 0, proc.stdout
    assert f"make {target} runs in CI" in proc.stderr
    assert "ARGUS_ALLOW_LOCAL=1" in proc.stderr
    assert first_command not in proc.stdout


@pytest.mark.parametrize(
    "extra",
    [{"CI": "true"}, {"ARGUS_ALLOW_LOCAL": "1"}],
    ids=["ci", "override"],
)
@pytest.mark.parametrize(("target", "first_command"), TARGETS)
def test_runs_in_ci_or_with_override(
    target: str, first_command: str, extra: dict[str, str]
) -> None:
    """CI, or the explicit override, lets the target reach its suite commands."""
    proc = run_make(target, extra)
    assert proc.returncode == 0, proc.stderr
    assert first_command in proc.stdout
    assert "runs in CI" not in proc.stderr
