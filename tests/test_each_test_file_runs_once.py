# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each test file runs exactly once across the verify workflow.

A file that runs twice doubles its wall time. A file that runs zero times is not
checked at all. This test expands every workflow step through the Makefile and
counts the test files each command runs: a pytest run over a directory runs every
file that collects a node, a pytest run over a file runs that file, and
`python tests/<name>.py` runs that script.
"""

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from ci_guard import (
    ROOT,
    SKIPPED,
    Step,
    pytest_argv,
    step_commands,
    target_files,
    workflow_steps,
)

TESTS = ROOT / "tests"
# A zero-node module collects nothing under pytest. Its one run is the script the
# self-test runs. The other zero-node module is imported by another test and runs
# on its own zero times.
SCRIPT_ONLY = {"tests/test_entrypoint.py"}


def rel(path: Path) -> str:
    """The path relative to the repository root, with forward slashes."""
    return path.resolve().relative_to(ROOT).as_posix()


def runs_of(argv: list[str], workdir: str) -> list[str]:
    """The test files one command runs."""
    parsed = pytest_argv(argv)
    if parsed is not None:
        files = {rel(path) for path in target_files(parsed, workdir, ROOT)}
        # A pytest run never runs a zero-node module, so it does not count.
        return sorted(files - set(SKIPPED))
    words = argv[2:] if argv[:2] == ["uv", "run"] else argv
    if len(words) > 1 and words[0] == "python" and words[1].startswith("tests/"):
        return [words[1]] if words[1].endswith(".py") else []
    return []


def count_runs(steps: Iterable[Step], makefile: str) -> Counter[str]:
    """How many times each test file runs across the given workflow steps."""
    counts: Counter[str] = Counter()
    for step in steps:
        for argv in step_commands(step.run, makefile):
            if argv:
                counts.update(runs_of(argv, step.workdir))
    return counts


def test_every_test_file_runs_exactly_once() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    counts = count_runs(workflow_steps(), makefile)
    problems: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        name = rel(path)
        want = 1 if name not in SKIPPED or name in SCRIPT_ONLY else 0
        if counts[name] != want:
            problems.append(f"{name}: runs {counts[name]} time(s), expected {want}")
    assert not problems, "\n".join(problems)


def test_a_file_named_twice_is_counted_twice() -> None:
    """The counter itself: a duplicate run must not pass as a single run."""
    steps = [
        Step(job="whole", run="uv run pytest -q tests/", workdir=""),
        Step(job="again", run="uv run pytest -q tests/test_gh_write.py", workdir=""),
    ]
    counts = count_runs(steps, "")
    assert counts["tests/test_gh_write.py"] == 2
    assert counts["tests/test_config.py"] == 1
    assert "tests/test_entrypoint.py" not in counts
