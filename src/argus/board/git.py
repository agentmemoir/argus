# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The git command runner, the remote facts, and the public/private boundary."""

import os
import re
import subprocess
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from argus import conventions
from argus.board.core import GIT_TIMEOUT_S, CoordinationUnavailable


def git_binary() -> str:
    """The git command named in the loaded config."""
    return conventions.setting("commands.git")


def isolate_self_test_git() -> None:
    """Run git with one fixed configuration for the rest of this self-test run.

    A self-test builds scratch repositories and runs hundreds of git commands in
    them. Without this, each of those commands also reads this machine's global
    and system config, so a machine-wide ``core.hooksPath`` starts its hooks
    inside every fixture: a host whose hooks run a script pays a process per hook
    per git call, which made the readiness report several times slower, and a
    hook that fails makes both self-tests fail for a reason that is not in Argus.
    ``GIT_CONFIG_GLOBAL`` at ``os.devnull`` and ``GIT_CONFIG_NOSYSTEM`` leave only
    the repository's own config, which is where every fixture's identity already
    lives; the installed-package exercise sets the same two variables.

    Repository-local config outranks both, so the repository-local
    ``core.hooksPath`` the rejected-push check installs keeps working, and the
    empty repository-local identity the failed-commit check sets is still the
    last word.

    Self-tests only. A real run's git calls keep the machine's configuration.
    """
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


def _git(
    repo: str | Path, *args: str, timeout: int = GIT_TIMEOUT_S
) -> tuple[int, str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            [git_binary(), "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CoordinationUnavailable("git is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CoordinationUnavailable(
            f"git {' '.join(args)} timed out after {timeout}s"
        ) from exc
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_git_repo(path: str | Path) -> bool:
    rc, out, _ = _git(path, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def _remote_urls(repo: str | Path, remote: str, *, push: bool = False) -> list[str]:
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.extend(["--all", remote])
    rc, out, _ = _git(repo, *args)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _all_remote_urls(repo: str | Path, *, push: bool = False) -> list[str]:
    """Every fetch (or push) URL of every remote the checkout configures.

    Separation is a property of the two repositories' configured destinations,
    not of the branch either happens to be on, so the proof reads all of them.
    """
    rc, out, _ = _git(repo, "remote")
    if rc != 0:
        return []
    urls: list[str] = []
    for name in out.splitlines():
        if name.strip():
            urls.extend(_remote_urls(repo, name, push=push))
    return urls


def _canonical_remote_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value and not re.match(r"^[^/]+@[^:]+:", value):
        return str(Path(value).expanduser().resolve())
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme:
        return value
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.query,
            parsed.fragment,
        )
    )


def _url_sets(repo: str | Path) -> tuple[set[str], set[str]]:
    """The canonical fetch and push destinations of every remote on ``repo``."""
    fetch = {_canonical_remote_url(url) for url in _all_remote_urls(repo)}
    push = {_canonical_remote_url(url) for url in _all_remote_urls(repo, push=True)}
    return fetch, push


def _assert_separate_destinations(checkouts: Mapping[str, Path]) -> None:
    """Refuse when two named checkouts share a Git push/fetch destination.

    Separation is a property of the configured destinations, read from every
    remote, not of the branches or the layout. Every checkout must carry both a
    fetch and a push URL, or the proof is impossible and the check fails closed.
    """
    destinations = {role: _url_sets(path) for role, path in checkouts.items()}
    for role, (fetch, push) in destinations.items():
        if not fetch or not push:
            raise CoordinationUnavailable(
                f"cannot prove separate Git destinations for the {role} repository"
            )
    roles = sorted(destinations)
    for index, left in enumerate(roles):
        left_fetch, left_push = destinations[left]
        for right in roles[index + 1 :]:
            right_fetch, right_push = destinations[right]
            if (
                left_push & right_push
                or left_push & right_fetch
                or left_fetch & right_push
            ):
                raise CoordinationUnavailable(
                    f"{left} and {right} repositories share a Git "
                    "push/fetch destination"
                )


def _assert_private_boundary(
    root: str | Path, private: str | Path, phases: str | Path | None = None
) -> None:
    # Only a directory that is its own work-tree top level is the public
    # repository. `git rev-parse --is-inside-work-tree` is true for a plain
    # directory that merely sits inside an enclosing repository, so testing the
    # public side with it read the enclosing repository's remotes, and a
    # checkout with no matching remote refused at construction with "cannot
    # prove separate destinations". The deliberate missing-remote case belongs
    # at read(), not here. Accepted trade-off: a repo-root configured as a
    # subdirectory of a repository is no longer boundary-checked; the supported
    # layout is a checkout root, which is its own top level.
    resolved = Path(root).resolve()
    rc_t, top, _ = _git(root, "rev-parse", "--show-toplevel")
    if rc_t != 0 or not top or Path(top).resolve() != resolved:
        return
    checkouts = {"public": Path(root), "board": Path(private)}
    if phases is not None:
        checkouts["phases"] = Path(phases)
    _assert_separate_destinations(checkouts)


def _head(repo: str | Path) -> str | None:
    rc, out, _ = _git(repo, "rev-parse", "HEAD")
    return out if rc == 0 and out else None


__all__ = [
    "_assert_private_boundary",
    "_assert_separate_destinations",
    "_git",
    "_head",
    "_is_git_repo",
    "isolate_self_test_git",
]
