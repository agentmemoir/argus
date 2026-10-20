# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""What must be true before open-pr asks gh to create anything.

Three facts decide whether a create can be right: the title and body the caller
wrote, whether this checkout is a fork, and whether the remote has the branch.
Each one is a refusal with a way out, and each is read before any create.
"""

from pathlib import Path

from argus.gitsync.gh_read import gh_repository_is_fork
from argus.gitsync.git import ConfigError, git


def text_from_file(path: str | Path | None, flag: str) -> str | None:
    """The text a caller put in a file, or None when no file was named.

    Raises:
        ConfigError: The file is missing or empty. Sending gh a title or body the
            caller did not write would publish a pull request nobody asked for.
    """
    if path is None:
        return None
    source = Path(path)
    if not source.is_file():
        raise ConfigError(f"{flag} {str(source)!r} does not exist")
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise ConfigError(f"{flag} {str(source)!r} is empty")
    return text


def fork_refusal(root: Path) -> str | None:
    """Why this checkout is a fork, or None when it is not one.

    ``gh pr list --head <branch>`` asks the checkout's own repository, and in a
    fork that repository is the fork, so the answer is a list with the branch's
    pull requests missing and a create that duplicates one in the base. This mode
    cannot name a fork's head branch, so it refuses instead of guessing.

    A gh that cannot answer is not read as a fork here: the list read still
    stops the create on an unreadable answer, so an unknown changes nothing and
    is not claimed either way.
    """
    is_fork, _ = gh_repository_is_fork(root)
    if not is_fork:
        return None
    return (
        f"{root} is a fork checkout; open-pr lists and creates pull requests in "
        "one repository, and a fork's branch needs its owner in the head, so it "
        "would list nothing and open a duplicate. Open the pull request from a "
        "checkout of the repository the branch's commits belong to, or by hand: "
        "gh pr create --repo <base owner/repo> --head <fork owner>:<branch>"
    )


def unpushed_refusal(root: Path, branch: str) -> str | None:
    """Why the remote does not have ``branch`` yet, or None when it does.

    gh creates a pull request from a branch the remote already has, so a branch
    that was never pushed comes back as a bare gh exit and no way out. The two
    checks are local: the refs this checkout keeps for ``origin``, which record
    what the remote had at the last fetch or push.

    That is a gap worth naming: a branch pushed from another checkout and not
    fetched here reads as never pushed, so the message offers the fetch as well
    as the push. A checkout with no ``origin`` remote is left to gh, which fails
    on it with its own words.
    """
    rc, url, _ = git(root, "remote", "get-url", "origin")
    if rc != 0 or not url.strip():
        return None
    local = git(root, "rev-parse", f"refs/heads/{branch}")
    remote = git(root, "rev-parse", f"refs/remotes/origin/{branch}")
    if remote[0] != 0:
        return (
            f"the branch {branch!r} has no ref refs/remotes/origin/{branch}, so "
            "this checkout has no record that the remote has it; push it before "
            f"the pull request: git push -u origin {branch}. If it was pushed "
            f"elsewhere, run git fetch origin first"
        )
    if local[1].strip() != remote[1].strip():
        return (
            f"the branch {branch!r} differs from origin/{branch}, so the remote "
            "does not have these commits; push it before the pull request: "
            f"git push origin {branch}"
        )
    return None


__all__ = ["fork_refusal", "text_from_file", "unpushed_refusal"]
