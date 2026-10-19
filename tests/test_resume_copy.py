# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Prove a copied run advances without touching the live run.

A phase that finished its developer step but never signalled is advanced from
the developer's own run log. This test copies the run, points glide at the copy,
and records the completion with the operator recovery path; it never opens a
harness and never writes the live run. Every input is named by the environment,
so no host path, phase number, or nonce is committed here.

The test skips unless these are set:

* ``ARGUS_COPY_RUN``      the run directory to copy (read-only input)
* ``ARGUS_COPY_PIPELINE`` the pipeline file the run belongs to
* ``ARGUS_GLIDE_PROJECT`` a glide checkout, run through ``uv``
* ``ARGUS_COPY_PHASE_FILE`` the phases file name the pipeline binds

A phase advance is recorded in ``run.json`` and the ledger; glide clears the
resume snapshot when it advances through ``--mark-done``, so that assertion is
made when the snapshot is present.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

LIVE = os.environ.get("ARGUS_COPY_RUN")
PIPELINE = os.environ.get("ARGUS_COPY_PIPELINE")
GLIDE = os.environ.get("ARGUS_GLIDE_PROJECT")
PHASE_FILE = os.environ.get("ARGUS_COPY_PHASE_FILE")

pytestmark = pytest.mark.skipif(
    not (LIVE and PIPELINE and GLIDE and PHASE_FILE),
    reason="set ARGUS_COPY_RUN, ARGUS_COPY_PIPELINE, ARGUS_GLIDE_PROJECT, "
    "and ARGUS_COPY_PHASE_FILE to run the copy proof",
)


def _developer_signal(log_path: Path) -> str:
    """The last DEVELOPER_DONE line the run log carries."""
    for line in reversed(log_path.read_text(errors="replace").splitlines()):
        stripped = line.strip()
        if stripped.startswith("DEVELOPER_DONE"):
            return stripped
    raise AssertionError(f"no DEVELOPER_DONE signal in {log_path}")


def _run_destination(pipeline: Path, number: int, phase_file: str) -> str:
    """The pipeline's run_dir for this phase, with its inputs filled in."""
    match = re.search(r"(?m)^run_dir:[ \t]*(\S+)", pipeline.read_text())
    template = match.group(1) if match is not None else ""
    try:
        return template.format(phase_number=number, phase_file=phase_file)
    except (KeyError, ValueError) as exc:
        raise AssertionError(f"cannot resolve the pipeline run_dir: {exc}") from exc


def _stub_bin(exec_dir: Path) -> Path:
    """A PATH entry whose harness commands fail and record that they ran.

    ``exec_dir`` is the probed directory the suite runs executables from, so
    the stubs run on a host that mounts its temporary storage ``noexec``.
    """
    bin_dir = exec_dir / "stub-bin"
    bin_dir.mkdir()
    sentinel = exec_dir / "harness-ran"
    for name in ("cmd", "opencode", "hermes", "agy", "cursor"):
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\nprintf '%s\\n' $0 >>{sentinel}\nexit 1\n")
        script.chmod(0o755)
    return bin_dir


def test_copied_run_advances_from_developer_to_adversary(
    tmp_path: Path, exec_dir: Path
):
    live = Path(str(LIVE))
    pipeline = Path(str(PIPELINE))
    glide = Path(str(GLIDE))
    name = live.name
    match = re.search(r"(\d{3,})", name)
    assert match, f"cannot read a phase number from {name}"
    number = int(match.group(1))

    log = live / "developer-task-r1.log"
    assert log.is_file(), f"the copied run has no developer run log: {log}"
    signal = _developer_signal(log)

    root = tmp_path / "root"
    destination = root / _run_destination(pipeline, number, str(PHASE_FILE))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(live, destination)

    stub = _stub_bin(exec_dir)
    sentinel = exec_dir / "harness-ran"
    hooks = tmp_path / "hooks.toml"
    hooks.write_text("")

    environment = dict(os.environ)
    environment["PATH"] = f"{stub}{os.pathsep}{environment.get('PATH', '')}"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(glide),
            "python",
            "-m",
            "glide",
            "run",
            "--pipeline",
            str(pipeline),
            "--repo",
            str(root),
            "--input",
            f"phase_number={number}",
            "--input",
            f"phase_file={PHASE_FILE}",
            "--hooks-override",
            str(hooks),
            "--mark-done",
            "developer",
            signal,
        ],
        cwd=glide,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])

    assert not sentinel.exists(), (
        "a harness ran during the recovery; the developer step re-ran: "
        f"{sentinel.read_text() if sentinel.exists() else ''}"
    )
    assert not list(destination.glob("developer-task-r2.*")), (
        "a second developer attempt was written"
    )

    run_result = json.loads((destination / "run.json").read_text())
    assert run_result.get("advanced_to") == "adversary", run_result
    ledger = json.loads((destination / "ledger.json").read_text())
    developer = ledger["steps"]["developer"]
    assert developer.get("routed_to") == "adversary", developer
    assert developer.get("visits") == 1, developer
    # The completion the ledger records is the step's own run log, by hash.
    log_text = (destination / "developer-task-r1.log").read_bytes()
    assert developer.get("output_sha256") == hashlib.sha256(log_text).hexdigest(), (
        developer.get("output_sha256")
    )

    resume_path = destination / "resume.json"
    if resume_path.is_file():
        resume = json.loads(resume_path.read_text())
        assert resume.get("current") == "adversary", resume
        assert resume.get("visits", {}).get("developer") == 1, resume
        outputs = resume.get("outputs") or {}
        assert "developer" in outputs, resume
        assert (
            hashlib.sha256(outputs["developer"].encode()).hexdigest()
            == developer["output_sha256"]
        )

    for marker in ("halted", "halted.json"):
        assert not (destination / marker).exists(), (
            f"a halt marker was written: {marker}"
        )
    # The live run is read-only input: it must be byte-for-byte where it was.
    assert (live / "resume.json").exists() or (live / "developer-task-r1.log").exists()
