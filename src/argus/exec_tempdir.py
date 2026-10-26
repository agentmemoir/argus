#!/usr/bin/env python3
"""A temporary directory whose scripts the host will actually run.

Why this exists
---------------
Two suites here stand in for a real command by writing an executable script into
a temporary directory and putting that directory earlier on PATH, or by writing a
hook into a throwaway git repository. Both only work when the filesystem holding
the file permits execution.

Some hosts mount the default temporary directory `noexec`. There the file mode is
correct and the mount refuses, so nothing runs and the failure is quiet in the
worst way:

- `execvp` treats a permission failure on one PATH entry as "keep looking" and
  walks on to the next one, so the real command answers instead of the stub. A
  test written to pin a defect then measures the host and reports a failure that
  names nothing in the tree — or, worse, passes because the host's real command
  happens to agree with what the stub would have said.
- git refuses to run a hook from such a mount and says the hook "is not set as
  executable", which names a permission that is in fact correct.

One rule, one place. Both suites call `exec_tempdir()` rather than each picking a
directory, because a second copy is a second chance to get it wrong. A host with
nowhere that permits execution skips with the reason, rather than reporting a
handful of failures about permissions that are correct.

Do not merge the two ways a probe can fail. A directory that refuses execution
and a probe that could not be run at all are different answers, and reading them
alike is a bug that has come back more than once. Only a `PermissionError`
raised while starting the probe is a refusal; a write that failed or a run that
timed out is **unprobed**. `can_execute` answers `False` for both on purpose,
because its callers only decide whether to try the next candidate;
`refuses_execution` is the only function that may claim a refusal.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import cast

SCRIPTS = Path(__file__).resolve().parent

#: Where a temporary directory is tried, in order.
#:
#: `None` is the default temporary directory, which is what `TMPDIR` or the
#: platform chooses. `/dev/shm` is the other place a writable, normally-temporary
#: directory lives. The build tree is last because it belongs to the toolchain: it
#: is already disposable, already kept out of version control, and already has
#: whatever cleanup policy the build has.
#:
#: The build tree is deliberately beside this helper's parent and not inside the
#: helper's own directory: a suite copies that directory into its fixture, so a
#: fixture created inside it would be copied into itself. A fresh checkout has no
#: build tree yet, so the directory is created on first use. A hardened host that
#: mounts its temporary directory and `/dev/shm` `noexec` has nowhere else that
#: permits execution, and a fixture that cannot run verifies nothing.
#:
#: A home directory is deliberately not a candidate. It is not scratch space, a
#: fixture tree left there outlives the run that made it, and a suite that fails
#: because of where the machine keeps its files has the defect this module exists
#: to remove.
BUILD_TREE = SCRIPTS.parent / "target"
CANDIDATE_PARENTS: tuple[str | None, ...] = (None, "/dev/shm", str(BUILD_TREE))


def _expand(parent: str | None) -> str | None:
    """The directory to create a temporary directory inside, or None for default."""
    return os.path.expanduser(parent) if parent is not None else None


def candidate_bases() -> list[str]:
    """Where a scratch directory may be made, as absolute paths, in the order tried.

    The same candidates `_executable_candidates` walks, so a caller looking for a
    directory this module made looks where it put one instead of keeping a second
    copy of the list. The first entry is the default temporary directory, which is
    what `TMPDIR` names when it is set: that is where a fixture really goes on
    macOS, and a sweep that named `/tmp` alone never found it there.
    """
    bases: list[str] = []
    for parent in CANDIDATE_PARENTS:
        base = _expand(parent)
        bases.append(tempfile.gettempdir() if base is None else base)
    return bases


#: The record every scratch directory carries: the pid of the process that made
#: it. `argus/driver/lib-selftest-recover.sh` reads it to tell a directory whose
#: owner is gone from one another run is using right now.
OWNER_RECORD = "owner"


def _record_owner(directory: str) -> None:
    """Name the process that owns this scratch directory, best effort.

    A directory with no record is reclaimed on age alone, which is what happened
    before the record existed, so a write that fails degrades to the old
    behaviour instead of turning a scratch directory into an error the caller
    never promised to handle.
    """
    try:
        (Path(directory) / OWNER_RECORD).write_text(
            f"pid={os.getpid()}\n", encoding="utf-8"
        )
    except OSError:
        pass


class _OwnedDirectory:
    """A temporary directory whose removal the caller controls.

    It answers the small part of `tempfile.TemporaryDirectory` these callers use —
    `.name`, `.cleanup()`, and use as a context manager — but nothing removes it
    unless asked. `TemporaryDirectory` registers a finaliser, and a finaliser dies
    with its process, so the directory would be gone before a caller in another
    process ever looked at it.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def cleanup(self) -> None:
        """Remove the directory and everything in it."""
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> _OwnedDirectory:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()

    def __fspath__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


class _ProbeOutcome(enum.Enum):
    """What running a probe executable in a directory showed about that directory.

    A three-valued enum, rather than two booleans or loose string flags, so each
    answer is named once and a caller cannot read an unprobed result as a refusal
    by accident.
    """

    #: The filesystem started the probe and it exited cleanly.
    RUNS = "runs"
    #: The kernel refused to start the probe. A mount with `noexec` gives this.
    #: It is the only answer that proves the directory itself refuses execution.
    REFUSED = "refused"
    #: The probe could not be run for another reason: it could not be written,
    #: the run timed out, or the run reported another error. Nothing is known
    #: about the directory, so this must never be read as a refusal.
    UNPROBED = "unprobed"


def _run_probe(directory: str) -> _ProbeOutcome:
    """Write a probe executable into `directory`, run it, and classify the result.

    Keep the two failures apart: a directory the kernel refuses to run a file in,
    and a probe that could not be run at all. Only the first is proof that the
    directory refuses execution; the second is a failure to find out, and reading
    it as a refusal turns a skip into a false answer.
    """
    # One name per thread and process. Parallel callers probe the same directory,
    # and a shared name lets one caller delete or overwrite another caller's probe.
    probe = os.path.join(directory, f"_probe-{os.getpid()}-{threading.get_ident()}.sh")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(probe, 0o755)
    except OSError:
        # A probe that could not be written was never run, so this says nothing
        # about execution. It is UNPROBED, never REFUSED.
        return _ProbeOutcome.UNPROBED
    try:
        done = subprocess.run(
            [probe], capture_output=True, text=True, timeout=10, check=False
        )  # noqa: S603
    except PermissionError:
        # Only a PermissionError raised while *starting* the probe is a refusal:
        # the kernel rejected the exec, which is what a `noexec` mount gives. A
        # timeout or any other OSError means the probe could not be run.
        return _ProbeOutcome.REFUSED
    except (OSError, subprocess.SubprocessError):
        return _ProbeOutcome.UNPROBED
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    return _ProbeOutcome.RUNS if done.returncode == 0 else _ProbeOutcome.UNPROBED


def can_execute(directory: str) -> bool:
    """Return True when an executable file in `directory` can actually be run.

    A hook and a stand-in command are both executable files, so the filesystem
    they live on has to permit execution. File permissions alone do not answer
    it, which is the whole reason this function exists: on a `noexec` mount the
    mode says yes and the kernel says no.

    Every failure answers False rather than raising, because the caller's decision
    is to try the next candidate, and a directory it cannot even write a probe
    into is a directory it cannot vouch for. False therefore covers both a
    directory that refuses execution and a probe that could not be run; a caller
    that needs the first claim alone asks `refuses_execution` instead.
    """
    return _run_probe(directory) is _ProbeOutcome.RUNS


def refuses_execution(directory: str) -> bool:
    """Return True when the filesystem itself refuses to run a file in `directory`.

    `can_execute` answers the same False for a mount that refuses execution and
    for a probe that failed transiently — a write it could not make, a run that
    timed out. A caller that needs the stronger claim, that the kernel really
    refused, asks this instead. Only a permission refusal from the kernel counts;
    a probe that could not be run proves nothing either way, so it answers False.
    This is the only place a refusal is claimed.
    """
    return _run_probe(directory) is _ProbeOutcome.REFUSED


def _executable_candidates(
    probe: Callable[[str], bool] | None,
    parents: Sequence[str | None] | None,
) -> Iterator[str]:
    """Yield a usable directory in each candidate that permits execution.

    One candidate is tried at a time and a directory that turns out to be unusable
    is removed before the next is tried, so a host with several bad candidates does
    not accumulate a directory in each of them. The generator ends without yielding
    when no candidate works.
    """
    can_run = probe or can_execute
    seen: set[str] = set()
    for parent in CANDIDATE_PARENTS if parents is None else parents:
        base = _expand(parent)
        key = base or "default"
        if key in seen:
            continue
        seen.add(key)
        if base is not None and base == str(BUILD_TREE):
            # The build tree is the one candidate this helper may create. A host
            # whose temporary directories are `noexec` reaches it only if the
            # directory exists, and a fresh checkout does not have one yet.
            try:
                os.makedirs(base, exist_ok=True)
            except OSError:
                continue
        try:
            directory = tempfile.mkdtemp(prefix="argus-selftest-", dir=base)
        except OSError:
            continue
        if can_run(directory):
            _record_owner(directory)
            yield os.path.realpath(directory)
        else:
            shutil.rmtree(directory, ignore_errors=True)


def make_exec_dir(
    probe: Callable[[str], bool] | None = None,
    parents: Sequence[str | None] | None = None,
) -> str:
    """Create a directory whose scripts this host will really run, and leave it in place.

    For a caller that hands the path to another process, which outlives this one.
    The caller removes the directory; nothing here does, because a finaliser bound
    to this process would delete the directory out from under that other process.

    `probe` is the executability check and `parents` is the list of candidates to
    search, both injectable so a caller can drive the rejection and exhaustion
    branches on a host whose temporary directory is in fact executable. Returns `""`
    when no candidate works, and does not raise: the callers here are not test
    runners, and they report the condition themselves.
    """
    return next(_executable_candidates(probe, parents), "")


def exec_tempdir(
    probe: Callable[[str], bool] | None = None,
    purpose: str = "an executable file",
    parents: Sequence[str | None] | None = None,
) -> tempfile.TemporaryDirectory[str]:
    """A temporary directory whose scripts the host will really run, removed with the suite.

    The candidates in `parents`, or `CANDIDATE_PARENTS` when that is not given, are
    tried in order and the first one that permits execution is returned, so a host
    whose default temporary directory is `noexec` still gets a usable one. A host
    with none of them skips: better one stated skip than failures that each blame a
    permission which is in fact correct.

    `purpose` names what the caller needed to run, so the skip reason says
    something useful instead of a generic sentence.

    `probe` is the executability check and `parents` is the list to search. Both
    are injectable so a suite can drive the rejection and exhaustion branches on a
    host whose temporary directory is in fact executable, which is the only way
    those branches are ever taken.
    """
    created = make_exec_dir(probe, parents)
    if not created:
        raise unittest.SkipTest(
            f"no temporary directory on this host permits running {purpose}"
        )
    # The owned directory answers the same protocol without inheriting.
    return cast("tempfile.TemporaryDirectory[str]", _OwnedDirectory(created))
