"""Read the CI workflows and decide whether one step runs the whole test tree.

This module holds the parsing helpers for `test_ci_runs_every_test_file.py` so
that guard stays readable and under the line limit.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

FILTERS = (
    "-k",
    "-m",
    "-p",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--collect-only",
)
SWALLOWED = ("|| true", "|| exit 0", "|| :")
SEPARATORS = ("uv run", "python -m", "python3 -m", "pytest")
# Script or fixture modules: each collects zero nodes by design. The value
# says where its code does run.
SKIPPED = {
    "tests/test_entrypoint.py": "Makefile:131 runs it as a script",
    "tests/test_issue_publication_fixture.py": "imported by test_issue_publication*",
}
TEST_MAKEFILE = "test:\n\t$(RUN) pytest -q tests/\n"


@dataclass(frozen=True)
class Step:
    """One workflow step, with the fields that can hide a narrowed run."""

    job: str
    run: str
    workdir: str = ""
    continue_on_error: bool = False


def logical_lines(text: str) -> list[str]:
    """Shell lines with comments dropped and `\\` continuations joined."""
    lines = [line.strip() for line in text.replace("\\\n", " ").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def make_recipe(makefile: str, target: str) -> str:
    """The tab-indented recipe body of `target`, or an empty string."""
    lines = makefile.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{target}:"):
            body: list[str] = []
            for follow in lines[index + 1 :]:
                if not follow.startswith("\t"):
                    break
                body.append(follow)
            return "\n".join(body)
    return ""


def step_commands(run: str, makefile: str) -> list[list[str]]:
    """Every argv a step runs, with `make <target>` expanded one level."""
    commands: list[list[str]] = []
    for line in logical_lines(run):
        argv = shlex.split(line)
        if argv[:1] != ["make"] or not makefile:
            commands.append(argv)
            continue
        for target in (arg for arg in argv[1:] if not arg.startswith(("-", "="))):
            for part in logical_lines(make_recipe(makefile, target)):
                # `$(RUN)` and friends are make variables, not argv words.
                recipe = [
                    word for word in shlex.split(part) if not word.startswith("$(")
                ]
                commands.append(recipe)
    return commands


def pytest_argv(argv: list[str]) -> list[str] | None:
    """`argv` from `pytest` on when pytest sits at an executable position."""
    rest = list(argv)
    while len(rest) > 1 and " ".join(rest[:2]) in SEPARATORS:
        rest = rest[2:]
    return rest if rest[:1] == ["pytest"] else None


def has_filter(argv: list[str]) -> bool:
    """True when a flag narrows, deselects, or only collects."""
    for arg in argv[1:]:
        for flag in FILTERS:
            if arg == flag or arg.startswith(f"{flag}="):
                return True
            if flag in ("-k", "-m", "-p") and arg.startswith(flag) and len(arg) > 2:
                return True
    return False


def target_files(argv: list[str], workdir: str, root: Path) -> set[Path]:
    """The test files the pytest path arguments resolve to, under `workdir`."""
    base = (root / workdir).resolve()
    args = [arg for arg in argv[1:] if not arg.startswith("-")]
    files: set[Path] = set()
    for arg in args or ["."]:
        path = (base / arg).resolve()
        if path.is_dir():
            files.update(one.resolve() for one in path.rglob("test_*.py"))
        elif path.is_file():
            files.add(path)
    return files


def runs_whole_tree(
    step: Step, every_file: set[Path], makefile: str, root: Path = ROOT
) -> bool:
    """True when this step runs pytest over every test file, un-filtered."""
    if step.continue_on_error or any(mark in step.run for mark in SWALLOWED):
        return False
    for argv in step_commands(step.run, makefile):
        parsed = pytest_argv(argv)
        if parsed is None or has_filter(parsed):
            continue
        if every_file <= target_files(parsed, step.workdir, root):
            return True
    return False


def steps_in(text: str) -> list[Step]:
    """Every step in one workflow document."""
    document = yaml.safe_load(text)
    data = cast("dict[str, object]", document) if isinstance(document, dict) else {}
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return []
    steps: list[Step] = []
    for name, job in cast("dict[str, object]", jobs).items():
        if not isinstance(job, dict):
            continue
        raw = cast("dict[str, object]", job).get("steps")
        if not isinstance(raw, list):
            continue
        entries = cast("list[object]", raw)
        for entry in entries:
            if isinstance(entry, dict):
                steps.append(step_from(str(name), cast("dict[str, object]", entry)))
    return steps


def step_from(job: str, step: dict[str, object]) -> Step:
    """One `Step` from its YAML mapping."""
    return Step(
        job=job,
        run=str(step.get("run") or ""),
        workdir=str(step.get("working-directory") or ""),
        continue_on_error=bool(step.get("continue-on-error")),
    )


def workflow_steps(directory: Path = WORKFLOWS) -> list[Step]:
    """Every step across all workflow files, with jobs merged."""
    steps: list[Step] = []
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        steps.extend(steps_in(path.read_text(encoding="utf-8")))
    return steps


def workflow(run: str, extra: str = "") -> str:
    """A one-job workflow whose single step runs `run`."""
    body = "".join(f"          {line}\n" for line in run.splitlines())
    return f"jobs:\n  ci:\n    steps:\n      - run: |\n{body}{extra}"
