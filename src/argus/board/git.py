# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The git command runner, the remote facts, and the public/private boundary."""

import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from argus import conventions
from argus.board.core import GIT_TIMEOUT_S, CoordinationUnavailable


def git_binary() -> str:
    """The git command named in the loaded config."""
    return conventions.setting("commands.git")


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


def _remote_url_sets(
    root: str | Path, private: str | Path, remote: str
) -> tuple[set[str], set[str], set[str], set[str]]:
    public_fetch = {_canonical_remote_url(url) for url in _remote_urls(root, remote)}
    public_push = {
        _canonical_remote_url(url) for url in _remote_urls(root, remote, push=True)
    }
    private_fetch = {
        _canonical_remote_url(url) for url in _remote_urls(private, remote)
    }
    private_push = {
        _canonical_remote_url(url) for url in _remote_urls(private, remote, push=True)
    }
    return public_fetch, public_push, private_fetch, private_push


def _assert_private_boundary(
    root: str | Path, private: str | Path, remote: str
) -> None:
    public_is_repo = _is_git_repo(root)
    if not public_is_repo:
        return
    public_fetch, public_push, private_fetch, private_push = _remote_url_sets(
        root, private, remote
    )
    if not public_fetch or not public_push or not private_fetch or not private_push:
        raise CoordinationUnavailable(
            "cannot prove separate public/private Git destinations"
        )
    if (
        public_push & private_push
        or public_push & private_fetch
        or public_fetch & private_push
    ):
        raise CoordinationUnavailable(
            "public and private repositories share a Git push/fetch destination"
        )


def _head(repo: str | Path) -> str | None:
    rc, out, _ = _git(repo, "rev-parse", "HEAD")
    return out if rc == 0 and out else None


__all__ = ["_assert_private_boundary", "_head"]
