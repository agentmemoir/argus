# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The board branch: settings, resolution, and branch refs."""

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
from argus.board.fetch_lock import fetch_serialized
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


def _branch_confirmed_absent(private: str | Path, remote: str, branch: str) -> bool:
    """Whether the remote answered and holds no board branch.

    ``ls-remote --exit-code`` answers 0 when the branch is there, and 2 when the
    remote answered with no such branch. Any other answer means the remote could
    not be asked. Only the second counts, so an unreachable board is never read
    as an empty one.
    """
    rc, _out, _err = _git(
        private,
        "ls-remote",
        "--exit-code",
        "--heads",
        remote,
        branch,
        timeout=FETCH_TIMEOUT_S,
    )
    return rc == 2


def board_never_created(private: str | Path, remote: str, branch: str) -> bool:
    """Whether the board was never created: no board branch and no backup either.

    A backup ref means the board existed and was deleted. That is a broken board,
    which must not read as an empty one and let a launch proceed while claims may
    still be held on the backup, so it stays a failure.
    """
    return _branch_confirmed_absent(
        private, remote, branch
    ) and _branch_confirmed_absent(private, remote, _backup_branch(branch))


def _backup_branch(branch: str) -> str:
    """The ref that mirrors the board branch, so a lost board can be restored.

    The board's only durable copy used to be the board branch itself, so
    deleting that branch deleted the board. Every board write advances this
    second ref, and a reseed reads it before it falls back to the integration
    branch. A configured ``board.backup-branch`` names it; otherwise the board
    branch's own name plus ``--argus-backup``. The doubled hyphen keeps the
    name clear of a repository's ordinary branches.
    """
    configured = conventions.setting("board.backup-branch", None)
    if configured:
        return _validate_branch(configured)
    return f"{branch}--argus-backup"


def _backup_source(private: str | Path, remote: str, branch: str) -> str | None:
    """The backup ref's commit, or ``None`` when the remote has no backup yet."""
    backup = _backup_branch(branch)
    if not _board_branch_exists(private, remote, backup):
        return None
    remote_ref = f"refs/remotes/{remote}/{backup}"
    rc, _out, err = _git(
        private,
        "fetch",
        "--quiet",
        remote,
        f"+refs/heads/{backup}:{remote_ref}",
        timeout=FETCH_TIMEOUT_S,
    )
    if rc != 0:
        raise CoordinationUnavailable(
            f"cannot fetch coordination backup {backup!r}: {_safe_detail(err)}"
        )
    rc, commit, err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc != 0 or not commit.strip():
        raise CoordinationUnavailable(
            f"cannot resolve coordination backup {backup!r}: "
            f"{_safe_detail(err or commit)}"
        )
    return commit.strip()


def _push_backup(private: str | Path, remote: str, branch: str, commit: str) -> None:
    """Advance the board's backup ref, or report why it could not.

    The board write has already succeeded, so a backup problem is reported to
    stderr and never raised: a stale backup must not fail the write it was
    meant to protect. The push is not forced, so a ref that exists but is not
    this board's backup is left untouched rather than overwritten.
    """
    backup = _backup_branch(branch)
    try:
        rc, out, err = _git(
            private,
            "push",
            remote,
            f"{commit}:refs/heads/{backup}",
            timeout=FETCH_TIMEOUT_S,
        )
    except CoordinationUnavailable as exc:
        # A timeout or a missing git binary leaves ``_git`` by raising rather
        # than by returning a non-zero code. The board write already succeeded,
        # so the backup problem is reported here too and never propagates.
        print(
            f"coordination backup ref {backup!r} was not advanced: "
            f"{_safe_detail(str(exc))}",
            file=sys.stderr,
        )
        return
    if rc != 0:
        print(
            f"coordination backup ref {backup!r} was not advanced: "
            f"{_safe_detail(out or err)}",
            file=sys.stderr,
        )


def _backfill_backup(private: str | Path, remote: str, branch: str) -> None:
    """Give an existing board branch a backup ref if it has none yet.

    A board created before the backup existed gains one on its next explicit
    migration, rather than waiting for the next write.
    """
    backup = _backup_branch(branch)
    if _board_branch_exists(private, remote, backup):
        return
    remote_ref = f"refs/remotes/{remote}/{branch}"
    rc, _out, _err = _git(
        private,
        "fetch",
        "--quiet",
        remote,
        f"+refs/heads/{branch}:{remote_ref}",
        timeout=FETCH_TIMEOUT_S,
    )
    if rc != 0:
        return
    rc, commit, _err = _git(private, "rev-parse", "--verify", remote_ref)
    if rc == 0 and commit.strip():
        _push_backup(private, remote, branch, commit.strip())


def _fetch_branch_ref(
    private: str | Path, remote: str, branch: str, *, wait: bool = True
) -> tuple[str, str]:
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
    with fetch_serialized(private, wait=wait):
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
    "_backfill_backup",
    "_backup_branch",
    "_backup_source",
    "_board_branch_exists",
    "_branch_setting",
    "_fetch_branch_ref",
    "_integration_branch",
    "_integration_ref",
    "_push_backup",
    "_remote_setting",
    "_resolve_private_repo",
    "_state_initialized",
]
