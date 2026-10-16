# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The hermetic self-test for the next-work selector, moved out of the entry file."""

import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from argus.phases.source import self_test as phases_source_self_test
from argus.select.next_phase import main
from argus.select.work_source import WorkSourceError, resolve
from argus.select.work_source import self_test as work_source_self_test


def self_test() -> None:
    phases_source_self_test()
    work_source_self_test()
    _self_test_configuration()
    _self_test_group_query()
    print("next_phase self-test: pass")


def _self_test_configuration() -> None:
    """This file's own wiring: which source is chosen, and what a bad one does.

    The source's rules are tested where they live. What is tested here is the
    seam between the driver's command line and the source it reaches, because a
    machine that named a folder that does not exist must stop rather than read
    the one that does.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Everything lives under this one temporary directory, and the working
        # directory the test chdirs into is inside it too. A fixture that
        # reached outside would collide with the previous run's leftovers in
        # the shared temporary parent and fail on the second run rather than
        # on the first, which is how this passed once and then did not.
        repo = Path(tmp) / "repo"
        repo.mkdir()
        elsewhere = Path(tmp) / "elsewhere"
        elsewhere.mkdir()
        (repo / "phases").mkdir()
        (repo / "phases" / "index.md").write_text("Plan ready\n")
        (repo / "phases" / "phase-100440-first.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )

        # Unconfigured is the phases source reading the derived folder, which
        # is what the source returns when the caller gives it a folder.
        default = resolve(None, repo, repo / "phases")
        assert default.name == "phases", default.name
        assert default.phases == repo / "phases", default.phases

        # Named explicitly, the same source is chosen.
        assert resolve("phases", repo, repo / "phases").name == "phases"

        # A folder inside the repository is read instead of the derived one.
        named = repo / "named"
        named.mkdir()
        (named / "phase-100450-named.md").write_text("x\n")
        assert resolve("phases", repo, named).phases == named

        # A relative configured folder resolves against the repository root and
        # not the working directory. Cron starts the driver from an arbitrary
        # directory, and the driver's own shell anchors the same way, so the two
        # must never disagree about which folder a name refers to.
        os.chdir(elsewhere)
        try:
            resolved = resolve("phases", repo, "named")
        finally:
            os.chdir(repo)
        assert resolved.phases == named, resolved.phases

        # A name nobody recognises is refused, and the refusal names what is
        # available rather than falling back to a source that would work.
        try:
            resolve("unknown-source", repo)
            refused = False
        except WorkSourceError as exc:
            refused = True
            assert "phases" in str(exc), exc
        assert refused, "an unknown work source must not fall back"

        # A folder that cannot be read stops the tick, with the folder named.
        try:
            resolve("phases", repo, repo / "absent")
            refused = False
        except WorkSourceError as exc:
            refused = True
            assert "absent" in str(exc), exc
        assert refused, "an unreadable phases folder must not fall back"

    # main() turns both refusals into exit 2 with a message on stderr and
    # nothing on stdout, so a caller reading the result sees an error and not
    # an empty selection. Output is captured: the self-test must not add
    # lines to the --check report that runs it.
    with tempfile.TemporaryDirectory() as tmp:
        for argv, expected in (
            (["--repo", tmp, "--source", "unknown-source"], "unknown work source"),
            (
                ["--repo", tmp, "--phases", str(Path(tmp) / "absent")],
                "configured phases folder",
            ),
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    argv,
                    _settings={
                        "repo-root": Path(tmp),
                        "pipeline": None,
                        "phases-dir": Path(tmp) / "phases",
                        "driver.include": ["phase-*.md"],
                        "driver.exclude": [],
                        "driver.runs": Path(tmp) / "runs",
                        "machine.id": "self-test",
                    },
                )
            assert code == 2, (argv, code)
            assert expected in err.getvalue(), (argv, err.getvalue())
            assert out.getvalue() == "", (argv, out.getvalue())

    # Repeated --phase-glob flags must reach the source as one include list,
    # and an excluded name must not be listed. The driver passes one flag per
    # configured include, so accepting only the last value would silently drop
    # every other glob.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "phases").mkdir()
        (repo / "phases" / "phase-100460-first.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        (repo / "phases" / "work-100461-renamed.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        (repo / "phases" / "work-100462-skip.md").write_text(
            "- [ ] Required approval is obtained.\n"
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                [
                    "--repo",
                    str(repo),
                    "--source",
                    "phases",
                    "--phases",
                    str(repo / "phases"),
                    "--verbose",
                    "--phase-glob",
                    "phase-*.md",
                    "--phase-glob",
                    "work-*.md",
                ],
                _settings={
                    "repo-root": repo,
                    "pipeline": None,
                    "phases-dir": repo / "phases",
                    "driver.include": ["ignored-*.md"],
                    "driver.exclude": ["work-100462-skip.md"],
                    "driver.runs": repo / "runs",
                    "machine.id": "self-test",
                },
            )
        assert code == 0, (code, err.getvalue())
        assert "phase-100460-first.md" in err.getvalue(), err.getvalue()
        assert "work-100461-renamed.md" in err.getvalue(), err.getvalue()
        assert "work-100462-skip.md" not in err.getvalue(), err.getvalue()


def _self_test_group_query() -> None:
    """The read-only group query answers from the phases and needs no board.

    The settings below name no board at all. That absence is the proof that
    matters: if the query reached the board it would fail on the missing
    configuration, so a clean answer shows the read path never touches it. The
    second case proves a phase with no declaration is refused by name.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        phases = repo / "phases"
        phases.mkdir()
        for number in (100010, 100020, 100030):
            (phases / f"phase-{number}-x.md").write_text(
                "### Reservation Group\n\n`together: 100010, 100020, 100030`\n"
            )
        (phases / "phase-100040-y.md").write_text("## 1. Objective\n")
        rundir = repo / "runs" / "phase-100010"
        rundir.mkdir(parents=True)
        (rundir / "run.json").write_text(json.dumps({"state": "completed"}))
        settings = {
            "repo-root": repo,
            "pipeline": None,
            "phases-dir": phases,
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": repo / "runs",
            "machine.id": "self-test",
        }
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--resolve-group", "100020"], _settings=settings)
        assert code == 0, (code, out.getvalue(), err.getvalue())
        result = json.loads(out.getvalue())
        assert result["state"] == "GROUP", result
        assert result["group"] == ["100010", "100020", "100030"], result
        assert result["remainder"] == ["100020", "100030"], result
        assert not (repo / "vault").exists(), (
            "the group query must not create a board checkout"
        )

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--resolve-group", "100040"], _settings=settings)
        assert code == 2, (code, out.getvalue(), err.getvalue())
        result = json.loads(out.getvalue())
        assert result["state"] == "GROUP_UNAVAILABLE", result
        assert "no '### Reservation Group' section" in result["error"], result
        assert result["document"].endswith("phase-100040-y.md"), result
