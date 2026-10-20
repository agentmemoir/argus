# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The base branch a draft pull request targets, resolved honestly.

``integration_branch`` answers with a name even when the checkout cannot prove
one: it falls back to ``master``. That is the right answer for a sync, where a
wrong name fails loudly against the local remote. It is the wrong answer for
``gh pr create``, where a ``main``-default repository gets ``--base master`` and
a real host returns a bare gh exit that names nothing.

So this module resolves in a stated order and refuses at the end of it: the
explicit ``--base``, then the checkout's own answer, then the repository's
default branch from gh, then a refusal that names ``--base`` as the way out.

``integration_branch`` is left as it is for the other modes.
"""

from pathlib import Path

from argus.gitsync.gh import gh_default_branch
from argus.gitsync.git import INTEGRATION_BRANCH, ConfigError, git, is_git_repo


def local_integration_branch(root: str | Path) -> str | None:
    """The integration branch this checkout can prove, or None.

    The expected name comes first, so a host that renamed its integration
    branch does not silently get a second one. Failing that, the remote's own
    default branch is the answer a fresh clone reports.
    """
    repo = Path(root)
    if not is_git_repo(repo):
        return None
    if git(repo, "rev-parse", "--verify", INTEGRATION_BRANCH)[0] == 0:
        return INTEGRATION_BRANCH
    rc, out, _ = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out:
        return out.rsplit("/", 1)[-1]
    return None


def resolve_base(root: str | Path, base: str | None) -> str:
    """The branch to open the pull request into.

    An explicit ``--base`` wins, and nothing else is asked. Then the checkout's
    own answer. Then the repository's default branch from gh, which is the one
    source that knows a default branch this clone never fetched.

    Raises:
        ConfigError: No base branch could be resolved. The message names
            ``--base``, because that is the way out.
    """
    if base:
        return str(base)
    local = local_integration_branch(root)
    if local is not None:
        return local
    found, why = gh_default_branch(Path(root))
    if found:
        return found
    raise ConfigError(
        f"cannot resolve the base branch for {root}: the checkout has no "
        f"{INTEGRATION_BRANCH!r} branch and no origin/HEAD, and gh could not "
        f"name the repository's default branch ({why}); pass --base <branch> "
        "to name the branch to open the pull request into"
    )


__all__ = ["local_integration_branch", "resolve_base"]
