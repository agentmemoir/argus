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
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

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


def can_execute(directory: str) -> bool:
    """Return True when an executable file in `directory` can actually be run.

    A hook and a stand-in command are both executable files, so the filesystem
    they live on has to permit execution. File permissions alone do not answer
    it, which is the whole reason this function exists: on a `noexec` mount the
    mode says yes and the kernel says no.

    Every failure answers False rather than raising, because the caller's decision
    is to try the next candidate, and a directory it cannot even write a probe
    into is a directory it cannot vouch for.
    """
    probe = os.path.join(directory, "_probe.sh")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(probe, 0o755)
    except OSError:
        return False
    try:
        done = subprocess.run([probe], capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    return done.returncode == 0


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
        if base == str(BUILD_TREE):
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
) -> tempfile.TemporaryDirectory:
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
        raise unittest.SkipTest(f"no temporary directory on this host permits running {purpose}")
    return _OwnedDirectory(created)
