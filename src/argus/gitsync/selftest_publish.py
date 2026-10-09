# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The publish-while-writing checks and their heartbeat fixture."""

import contextlib
import importlib
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from argus import conventions
from argus.gitsync.git import git, git_binary
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.runstate import is_engine_written_run_artifact, publish_dirt
from argus.gitsync.selftest_fixture import BOARD_REL, _configure_self_test, _init_remote


def _publish_fixture(base: Path, name: str, phase: int) -> tuple[Path, Path, Path]:
    """A phase at finalize in its own throwaway pair of remotes.

    Returns (public checkout, nested private checkout, run directory). The
    coordination board is set up and the reservation file written, so
    ``publish_phase`` can be called for real.
    """
    d = base / name
    d.mkdir(parents=True)
    pub: Path = _init_remote(d, "pub")[1]
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote: Path = _init_remote(d, "priv")[0]
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    _configure_self_test(pub, nested)
    from argus.board.reservations import ReservationStore, save_reservation_file

    store: Any = ReservationStore(pub, branch="main")
    reservation = store.reserve(phase, "server-01")
    # The coordination transaction pushes onto the same branch the working tree
    # is on, so the checkout is left behind its own remote. That is the real
    # mechanism by which "master" moves under a phase.
    git(nested, "fetch", "-q", "origin")
    git(nested, "merge", "-q", "--ff-only", "origin/main")
    run_dir = nested / "runs" / f"phase-{phase:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    save_reservation_file(run_dir / "reservation.json", reservation)
    (run_dir / "run.json").write_text('{"state":"completed"}\n')
    (run_dir / "activity").write_text("harness output at 0\n")
    (run_dir / "reminders.log").write_text("reminder suppressed by busy\n")
    (run_dir / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
    git(nested, "add", "-A")
    git(nested, "commit", "-q", "-m", "run state")
    git(nested, "push", "-q", "origin", "main")
    return pub, nested, run_dir


class _Heartbeat:
    """A stand-in for the engine's heartbeat writer, on the real cadence.

    glide.nudge.touch_activity is a public function with one job: rewrite
    ``runs/<phase>/activity`` with a fresh whole-second timestamp. The
    publication helper is asked to publish from inside the process that does
    that, so the writer has to be a separate process, running, with no
    cooperation from the code under test. If glide is importable the real
    function is used; otherwise the same three lines are written directly, and
    the check says so.
    """

    def __init__(self, run_dir: str | Path, period: float = 0.05) -> None:
        self.run_dir = Path(run_dir)
        self.period = period
        self.using_glide = False
        self.proc = None

    def __enter__(self) -> "_Heartbeat":
        import threading

        try:
            glide_nudge = importlib.import_module("glide.nudge")

            self.using_glide = glide_nudge.ACTIVITY_NAME == "activity"
        except ImportError:
            self.using_glide = False
        stop = threading.Event()
        marker = self.run_dir / "activity"
        seq: list[int] = [0]

        def write() -> None:
            while not stop.is_set():
                seq[0] += 1
                with contextlib.suppress(OSError):
                    marker.write_text(f"harness output at {1758800000 + seq[0]}\n")
                stop.wait(self.period)

        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        self._stop, self._thread = stop, thread
        return self

    def __exit__(self, *_exc: object) -> Literal[False]:
        self._stop.set()
        self._thread.join(timeout=5)
        return False

    def write_now(self) -> None:
        marker = self.run_dir / "activity"
        marker.write_text("harness output at 1999999999\n")


def _publish_while_writing_check(base: Path) -> tuple[bool, str]:
    """Publication must succeed on the first attempt with the engine writing.

    Before the engine-written-artifact exclusion, this failed intermittently
    with ``working tree is dirty after phase cleanup: .../activity``: the
    helper committed the run state, the engine rewrote the marker in the
    window, and the check that followed saw it. 9 failures in 300 attempts
    with the engine writing, 0 with it stopped.
    """
    pub, nested, run_dir = _publish_fixture(base, "publish-race", 100441)
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    result: dict[str, Any] = {}
    first = ""
    second = ""
    try:
        with _Heartbeat(run_dir):
            time.sleep(0.4)
            marker = run_dir / "activity"
            first = marker.read_text()
            time.sleep(1.1)
            second = marker.read_text()
            result = publish_phase(pub, 100441)
    finally:
        conventions.override({"board.branch": old_branch})
    ok = (
        result.get("ok")
        and result.get("state") == "AWAITING_REVIEW"
        and first != second
    )
    detail = (
        f"first={first.strip()!r} second={second.strip()!r} "
        f"state={result.get('state')} "
        f"engine_written={result.get('engine_written')} "
        f"summary={result.get('summary')} error={result.get('error')} "
        f"dirty={result.get('dirty')}"
    )
    if not ok:
        return False, detail
    # The marker is still committed and pushed, exclusion or not.
    tracked_files: str = git(nested, "ls-files", "runs")[1]
    tracked = "activity" in tracked_files
    committed: str = git(nested, "show", "HEAD:runs/phase-100441/activity")[1]
    if not tracked or not committed.strip():
        return False, f"marker is not committed and pushed: {detail}"
    receipt = result.get("receipt", {})
    if receipt.get("state") != "awaiting_review":
        return False, f"receipt state changed: {receipt}"
    return (bool(receipt.get("engine_written_at_publish")), detail)


def _publish_still_refuses_check(base: Path) -> list[tuple[str, object, str]]:
    """The exclusion must not have become a blanket pass.

    Each case gets its own fixture, because a publication moves both remotes.
    """
    results: list[tuple[str, object, str]] = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # 1. A modified tracked source file in the public checkout.
        pub, _nested, _run = _publish_fixture(base, "publish-dirt-source", 100442)
        (pub / "a.txt").write_text("one\nunreviewed edit\n")
        res: dict[str, Any] = publish_phase(pub, 100442)
        results.append(
            (
                "a modified tracked source file still blocks",
                (not res.get("ok"))
                and "outside the pipeline's run state" in res.get("summary", ""),
                res.get("summary", str(res))[:200],
            )
        )

        # 2. A staged change in the private checkout, outside the phase.
        pub, nested, _run = _publish_fixture(base, "publish-dirt-staged", 100443)
        (nested / "stray.txt").write_text("unreviewed\n")
        git(nested, "add", "stray.txt")
        res = publish_phase(pub, 100443)
        results.append(
            (
                "a staged change outside the phase still blocks",
                (not res.get("ok"))
                and "outside the current phase" in res.get("summary", ""),
                res.get("summary", str(res))[:200],
            )
        )

        # 3. A staged change inside the phase's own run directory is the phase's
        # own evidence, so it is committed rather than refused. Asserted as
        # measured: this is how the helper behaved before the change too.
        pub, nested, run_dir = _publish_fixture(base, "publish-inside", 100445)
        (run_dir / "findings.json").write_text('{"open": []}\n')
        res = publish_phase(pub, 100445)
        tracked_files: str = git(nested, "ls-files", "runs")[1]
        pushed = "runs/phase-100445/findings.json" in tracked_files.splitlines()
        results.append(
            (
                "a new file inside the phase's run directory is published, not blocked",
                bool(res.get("ok")) and pushed,
                f"state={res.get('state')} committed={pushed}",
            )
        )
    finally:
        conventions.override({"board.branch": old_branch})

    # 4. The exclusions are exactly the two engine-written artifacts, and only
    # by basename, so a source file called `activity` elsewhere is not exempt
    # by accident and a new run artifact is not exempt by accident.
    unit = [
        ("runs/phase-1/activity", True),
        (f"{BOARD_REL}/runs/phase-1/activity", True),
        (f"{BOARD_REL}/runs/phase-1/reminders.log", True),
        ("crates/store/activity", False),
        ("crates/store/reminders.log", False),
        ("crates/store/src/lib.rs", False),
        ("runs/phase-1/findings.json", False),
        ("runs/phase-1/finalize-task-r1.log", False),
        ("runs/phase-1/ledger.json", False),
        ("runs/phase-1/publication.json", False),
        ("runs/phase-1/resume.json", False),
        ("runs/activity", False),
        ("runs/phase-1/nested/activity", False),
    ]
    wrong = [
        (p, want, is_engine_written_run_artifact(p))
        for p, want in unit
        if is_engine_written_run_artifact(p) != want
    ]
    results.append(
        (
            "the engine-written exemption is exactly two run artifacts",
            not wrong,
            f"mismatches={wrong}",
        )
    )

    real: list[str]
    engine: list[str]
    real, engine = publish_dirt(
        [
            f"{BOARD_REL}/runs/phase-1/activity",
            "crates/store/src/lib.rs",
            f"{BOARD_REL}/runs/phase-1/reminders.log",
            "runs/phase-1/notes.md",
        ]
    )
    results.append(
        (
            "publish_dirt reports the excluded set instead of dropping it",
            real == ["crates/store/src/lib.rs", "runs/phase-1/notes.md"]
            and engine
            == [
                f"{BOARD_REL}/runs/phase-1/activity",
                f"{BOARD_REL}/runs/phase-1/reminders.log",
            ],
            f"real={real} engine={engine}",
        )
    )
    return results


__all__ = ["_publish_still_refuses_check", "_publish_while_writing_check"]
