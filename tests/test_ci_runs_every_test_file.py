"""CI collects every `test_*.py` file under `tests/`.

The affected-checks step once named a hand-written list of test files, so a
test could sit outside it and never run. The step now runs the whole
directory; this guard fails if it narrows back to a list with a file missing.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
TESTS = ROOT / "tests"


def pytest_commands(workflow: Path) -> list[tuple[str, list[str]]]:
    """The job name and argv of every `run` line that invokes pytest."""
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    commands = []
    for name, job in jobs.items():
        for step in job["steps"]:
            for line in str(step.get("run", "")).splitlines():
                argv = line.split()
                if "pytest" in argv:
                    commands.append((name, argv))
    return commands


def collected(argv: list[str]) -> set[Path]:
    """Expand the path arguments after pytest to a set of test files."""
    files: set[Path] = set()
    for arg in argv[argv.index("pytest") + 1 :]:
        if arg.startswith("-"):
            continue
        target = ROOT / arg
        if target.is_dir():
            files.update(target.rglob("test_*.py"))
        else:
            files.update(path for path in ROOT.glob(arg) if path.is_file())
    return {path.resolve() for path in files}


def test_every_test_file_is_collected() -> None:
    """Each pytest step collects every test file, and the failure names misses."""
    every_file = {path.resolve() for path in TESTS.rglob("test_*.py")}
    commands = pytest_commands(WORKFLOW)
    assert commands, "no workflow step runs pytest"
    for job, argv in commands:
        missing = sorted(every_file - collected(argv))
        names = [str(path.relative_to(ROOT)) for path in missing]
        assert not missing, f"{job} runs {argv} and misses: {names}"


def test_affected_checks_collects_the_whole_tree() -> None:
    """The affected-checks step names the whole tests directory."""
    commands = [
        argv for job, argv in pytest_commands(WORKFLOW) if job == "affected-checks"
    ]
    assert commands, "the affected-checks job runs no pytest"
    for argv in commands:
        assert "tests/" in argv, f"affected-checks narrows to {argv}"
