"""CI runs the whole `tests/` tree, un-filtered, and every file collects.

The affected-checks step once named a hand-written list of test files, so a
test could sit outside it and never run. This guard fails when no job step
covers the tree, when a step narrows it with a filter flag, hides a failure,
or carries a line that only looks like pytest, and when a test file yields no
collected node.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from ci_guard import (
    SKIPPED,
    TEST_MAKEFILE,
    WORKFLOWS,
    runs_whole_tree,
    steps_in,
    workflow,
    workflow_steps,
)

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def collected_nodes() -> set[Path]:
    """Every test file that yields at least one node in a whole collection."""
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return {
        (ROOT / line.split("::", 1)[0]).resolve()
        for line in proc.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }


@pytest.fixture(scope="module")
def collected() -> set[Path]:
    """The test files that yield at least one node, collected once."""
    return collected_nodes()


def test_every_test_file_collects_a_node(collected: set[Path]) -> None:
    """Each test file yields a node, apart from the declared script modules."""
    assert collected, "pytest collected nothing under tests/"
    present = {
        one.relative_to(ROOT).as_posix(): one for one in TESTS.rglob("test_*.py")
    }
    empty = {name for name, path in present.items() if path.resolve() not in collected}
    assert empty <= set(SKIPPED), f"test files collect no node: {sorted(empty)}"
    assert set(SKIPPED) <= empty, f"stale allowlist: {sorted(set(SKIPPED) - empty)}"
    for name, reason in SKIPPED.items():
        assert reason, f"{name} needs a reason saying where it runs"


def test_some_step_runs_the_whole_tree(collected: set[Path]) -> None:
    """At least one job step runs every collected test file, un-filtered."""
    steps = workflow_steps(WORKFLOWS)
    assert steps, "no workflow has any step"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    whole = [s for s in steps if runs_whole_tree(s, collected, makefile)]
    assert whole, f"no step runs all of tests/: {[(s.job, s.run) for s in steps]}"


CASES = [
    ("uv run pytest -q tests/", "", True),
    ("uv run pytest -q tests/test_ci_only_targets.py", "", False),
    ("uv run pytest -q tests/ -k 'not slow'", "", False),
    ("uv run pytest -q tests/ --ignore tests/test_config.py", "", False),
    ("# uv run pytest -q tests/", "", False),
    ("echo pytest tests/", "", False),
    ("uv run pytest -q tests/ || true", "", False),
    ("uv run pytest -q tests/", "        continue-on-error: true\n", False),
    ("uv run pytest -q \\\n  tests/", "", True),
    ("pytest -q .", "        working-directory: tests\n", True),
    ("make test", "", True),
]


@pytest.mark.parametrize(
    ("run", "extra", "expected"), CASES, ids=[case[0][:24] for case in CASES]
)
def test_whole_tree_detection(
    run: str, extra: str, expected: bool, collected: set[Path]
) -> None:
    """Only a real, un-filtered pytest over the tree counts as the guard run."""
    steps = steps_in(workflow(run, extra))
    assert len(steps) == 1
    assert runs_whole_tree(steps[0], collected, TEST_MAKEFILE) is expected
