# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The board branch: settings, resolution, creation, and branch refs."""

import sys
from pathlib import Path

from argus import conventions
from argus.board.core import (
    FETCH_TIMEOUT_S,
    INTEGRATION_FALLBACK,
    CoordinationUnavailable,
    _safe_detail,
    _validate_branch,
    _validate_remote,
)
from argus.board.git import _git, _is_git_repo
from argus.board.state import state_rel
from argus.conventions import board_repo


def _branch_setting(branch: str | None) -> str:
    """Resolve the board's branch.

    A machine may name a different one. Nothing in this module can tell whether
    every machine agreed, because two boards on two branch names each look
    perfectly healthy from the inside, so the warning about it belongs to the
    driver, which is what a person reads. It is deliberately not printed here:
    callers merge this module's stderr into files they then parse as JSON, and
    a library that writes to a stream it was handed corrupts them.
    """
    return _validate_branch(branch or conventions.setting("board.branch"))


def _remote_setting(remote: str | None) -> str:
    return _validate_remote(remote or conventions.setting("board.remote"))


def _resolve_private_repo(
    repo_root: str | Path, private_repo: str | Path | None = None
) -> tuple[Path, Path]:
    root = Path(repo_root).resolve()
    named = private_repo
    private = Path(named).resolve() if named else board_repo(root)
    if private is None:
        raise CoordinationUnavailable("reservation board repository is not configured")
    if not private.is_dir() or not _is_git_repo(private):
        raise CoordinationUnavailable(
            f"configured board checkout is unavailable: {private}"
        )
    return root, private


def _integration_branch(private: str | Path, remote: str) -> str:
    """The branch run state and phase notes are published to.

    The board is not on it, so anything that has to confirm a *published* file
    has to ask this branch rather than the board's own. Asking the board's
    branch used to work only because the two were the same ref.
    """
    rc, out, _err = _git(
        private, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"
    )
    # --short answers with the remote-tracking short name, which still carries
    # the remote prefix; only the branch half is a branch.
    named = out.strip()
    if rc == 0 and named.startswith(f"{remote}/"):
        named = named[len(remote) + 1 :]
    if rc == 0 and named:
        return named
    return INTEGRATION_FALLBACK


def _integration_ref(private: str | Path, remote: str) -> str:
    """The commit a new board branch is seeded from.

    The board used to live on the integration branch, so the first board branch
    has to start from wherever that file is now. It must be the integration
    branch's own tip and nothing else: the local checkout's HEAD is whatever
    that checkout happens to be on, which during a phase run is a feature
    branch, so seeding from it would root the shared board's history at an
    arbitrary commit.

    The branch is fetched before it is resolved, because a clone that has never
    fetched that branch holds no remote-tracking ref for it and the answer would
    be the same arbitrary HEAD. Failing here is the right answer: seeding from a
    known ref is reproducible, and this is the one step that decides where every
    machine's board history begins.
    """
    branch = _integration_branch(private, remote)
    remote_ref = f"refs/remotes/{remote}/{branch}"
    _git(
        private,
        "fetch",
        "--quiet",
        remote,
        f"+refs/heads/{branch}:{remote_ref}",
        timeout=FETCH_TIMEOUT_S,
    )
    rc, commit, err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc == 0 and commit.strip():
        return commit.strip()
    raise CoordinationUnavailable(
        f"cannot resolve the integration branch {branch!r} on {remote}, "
        f"which is the only commit a new coordination branch may be seeded "
        f"from: {_safe_detail(err or commit)}"
    )


def _board_branch_exists(private: str | Path, remote: str, branch: str) -> bool:
    rc, _out, _err = _git(
        private,
        "ls-remote",
        "--exit-code",
        "--heads",
        remote,
        branch,
        timeout=FETCH_TIMEOUT_S,
    )
    return rc == 0


def ensure_board_branch(private: str | Path, remote: str, branch: str) -> bool:
    """Create the board's branch from the integration branch, once.

    Without this the first transaction after the change fails closed on every
    machine, because the branch the board moved to does not exist yet. Seeding
    copies the whole tree at the integration branch's tip, so the existing
    The configured board file comes with it and no live reservation is lost.
    The file stays on the integration branch afterwards as a historical record;
    nothing reads it there.

    This is the only function here that writes to the remote outside a board
    transaction, and it is deliberately not reachable from a read. A read that
    could create the branch would let an inspection of the board publish to the
    remote, which fails on a machine with read-only credentials and reports
    itself as a fetch failure rather than as a permission problem.

    Returns True when it created the branch. A machine that loses the race
    finds the branch already there and does nothing, which is the same answer.
    """
    if _board_branch_exists(private, remote, branch):
        return False
    source = _integration_ref(private, remote)
    rc, _out, err = _git(
        private,
        "push",
        remote,
        f"{source}:refs/heads/{branch}",
        timeout=FETCH_TIMEOUT_S,
    )
    if rc == 0:
        print(
            f"coordination branch {branch!r} seeded from {source[:12]}", file=sys.stderr
        )
        return True
    # Losing the seed race is success: the other machine made the branch.
    if _board_branch_exists(private, remote, branch):
        return False
    raise CoordinationUnavailable(
        f"cannot create coordination branch {branch!r} on {remote}: {_safe_detail(err)}"
    )


def _fetch_branch_ref(private: str | Path, remote: str, branch: str) -> tuple[str, str]:
    """Fetch one branch and return its remote-tracking ref and commit.

    A branch the remote does not have is the single most common answer here on a
    machine that has not migrated, so it is told apart from a transport failure.
    Git reports a missing branch as a failed fetch, and "coordination fetch
    failed" sent whoever read the log looking for a network problem instead of
    naming the one thing that creates it. The extra round trip is paid only when
    the fetch has already failed.
    """
    remote_ref = f"refs/remotes/{remote}/{branch}"
    refspec = f"+refs/heads/{branch}:{remote_ref}"
    rc, out, err = _git(
        private, "fetch", "--prune", remote, refspec, timeout=FETCH_TIMEOUT_S
    )
    if rc != 0:
        if not _board_branch_exists(private, remote, branch):
            raise CoordinationUnavailable(
                f"coordination branch {branch!r} does not exist on {remote}"
            )
        raise CoordinationUnavailable(
            f"coordination fetch failed: {_safe_detail(err or out)}"
        )
    rc, commit, err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc != 0 or not commit:
        raise CoordinationUnavailable(
            f"coordination branch {branch!r} does not exist on {remote}"
        )
    return remote_ref, commit


def _state_initialized(private: str | Path, ref: str, *, issues: bool = False) -> bool:
    shallow_rc, shallow_out, _shallow_err = _git(
        private, "rev-parse", "--is-shallow-repository"
    )
    if shallow_rc == 0 and shallow_out.strip() == "true":
        raise CoordinationUnavailable(
            "coordination state history is unavailable in a shallow repository"
        )
    rc, out, err = _git(
        private,
        "log",
        "--diff-filter=A",
        "--format=%H",
        "-n",
        "1",
        ref,
        "--",
        state_rel(issues=issues).as_posix(),
    )
    if rc != 0:
        raise CoordinationUnavailable(
            f"cannot inspect coordination state history: {_safe_detail(err or out)}"
        )
    return bool(out.strip())


__all__ = [
    "_branch_setting",
    "_fetch_branch_ref",
    "_integration_branch",
    "_remote_setting",
    "_resolve_private_repo",
    "_state_initialized",
]
