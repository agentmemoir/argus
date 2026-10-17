# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Receipts, phase run entries, pull request URLs, and the branch facts around them."""

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from argus.conventions import (
    phase_file_parts,
    phase_paths,
    phase_run_dir,
    runs_dir,
    setting,
)
from argus.gitsync.git import git
from argus.gitsync.paths import phases_folder

# Both push outcomes leave the branch at the result's ``after``: a push that
# moved the ref put that head on the branch, and one that had nothing to push
# leaves the branch already carrying it. Reading only ``pushed`` here would
# drop the head of an up-to-date push the moment the two outcomes were
# separated.
_PUSH_ACTIONS = ("pushed", "nothing-to-push")


def _published_head(results: list[dict[str, Any]], repo: str | Path) -> Any:
    for result in results:
        if result.get("repo") == str(repo) and result.get("action") in _PUSH_ACTIONS:
            return result.get("after")
    return None


def phase_receipt(repo: str | Path, number: int) -> dict[str, Any] | None:
    """A phase's publication receipt, or None when there is none.

    A receipt is the only record of where a phase's commits ended up, so it is
    read by the selector to decide whether the phase is still waiting for a
    human. A receipt that does not parse is treated as absent, not as
    completion: an unreadable receipt must never read as a merged phase.
    """
    path = phase_run_dir(repo, number) / "publication.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return cast("dict[str, Any]", data)


def head_tree_on_integration(checkout: str | Path, head: str, integration: str) -> bool:
    """Whether ``head``'s file content is already on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see it. What survives is the content: the squash commit's tree is
    the phase branch's tree, so the phase's work is present on the integration
    branch under a different commit id.

    This is the proof that still works when GitHub cannot be asked at all, which
    is the case that matters most — a missing or unauthenticated ``gh`` must
    degrade to a working answer, not to a line that never starts again.

    Returns True or False. False means "not found", which is the safe answer:
    the caller then falls back to the branch itself, so a phase that really was
    merged is never mistaken for one that was not.
    """
    rc, tree, _ = git(checkout, "rev-parse", "--verify", f"{head}^{{tree}}")
    if rc != 0 or not tree:
        return False
    rc, _, _ = git(checkout, "cat-file", "-e", f"{integration}^{{tree}}")
    if rc != 0:
        return False
    # The integration branch's own tip, checked first because it is the common
    # case and costs one comparison rather than a walk.
    rc, tip_tree, _ = git(checkout, "rev-parse", "--verify", f"{integration}^{{tree}}")
    if rc == 0 and tip_tree == tree:
        return True
    # Otherwise the squash commit's tree is somewhere in the branch's history.
    rc, found, _ = git(checkout, "log", "--format=%T", "-n", "200", integration)
    return rc == 0 and tree in found.split()


def _run_phase_number(entry: Path) -> int | None:
    parts = phase_file_parts(entry.name + ".md")
    if parts is not None:
        return int(parts[0])
    match = re.fullmatch(r"phase-(\d+)", entry.name)
    if match:
        return int(match.group(1))
    try:
        receipt = cast(
            "dict[str, Any]", json.loads((entry / "publication.json").read_text())
        )
        return int(receipt["phase"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _phase_run_entries(root: str | Path) -> Iterator[tuple[int, Path]]:
    if setting("pipeline", None) is not None:
        phases = phases_folder(root)
        for phase_file in phase_paths(phases):
            parts = phase_file_parts(phase_file)
            if parts is not None:
                entry = phase_run_dir(root, parts[0], phase_file)
                if entry.is_dir():
                    yield parts[0], entry
        return
    try:
        entries = sorted(runs_dir(root).iterdir())
    except OSError:
        return
    for entry in entries:
        number = _run_phase_number(entry)
        if number is not None and entry.is_dir():
            yield number, entry


# A pull request URL, as finalize records it. Only the four segments GitHub
# guarantees are captured: scheme, owner, repository, and the number under
# /pull/. A trailing path, a query, or a fragment is tolerated, because
# finalize pastes whatever URL the reviewer is looking at, and a URL the
# pipeline cannot parse is stored whole with no number so that
# ``gh_pull_request_state`` is simply not consulted.
#
# host is deliberately not pinned to github.com: GitHub Enterprise installs
# this pipeline too, and the host is passed through rather than matched. What
# the number is checked against is the receipt that named the URL, so a URL
# from another host still only ever confirms the phase's own pull request.
_PULL_REQUEST_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


def pull_request_ref(url: object) -> dict[str, Any] | None:
    """The (host, owner, repo, number) a pull request URL names, or None.

    Args:
        url: A pull request URL, or anything at all.

    Returns:
        A dict with ``host``, ``owner``, ``repo`` and ``number`` (an int), or
        None when the string is not a pull request URL. None is the normal
        answer for an empty or malformed value, not an error: the caller stores
        the URL for humans either way and falls back to the commit-id test.
    """
    match = _PULL_REQUEST_URL_RE.match(str(url or "").strip())
    if not match:
        return None
    return {
        "host": match.group("host"),
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "number": int(match.group("number")),
    }


def _head_carried_locally(
    checkout: str | Path, head: str, candidates: Iterable[str | None]
) -> str | None:
    """The first candidate ref in this checkout that carries ``head``, or None.

    Split out so the "try local first, fetch only if that failed" rule is one
    named thing rather than a loop that has to be read twice to see whether a
    fetch happened.
    """
    for candidate in candidates:
        if not candidate:
            continue
        rc, _, _ = git(checkout, "merge-base", "--is-ancestor", head, candidate)
        if rc == 0:
            return candidate
    return None


def receipt_for_branch(repo: str | Path, branch: str) -> dict[str, Any] | None:
    """The publication receipt whose ``branch`` is ``branch``, or None.

    The branch name is the join key, not the phase number, because the branch
    no longer carries the number. Scanning every run directory is deliberate:
    a receipt is the only evidence that a phase reached review, and a phase the
    operator has only written in the phases has none.
    """
    for _number, entry in _phase_run_entries(repo):
        try:
            data = json.loads((entry / "publication.json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        receipt = cast("dict[str, Any]", data)
        if receipt.get("branch") == branch:
            return receipt
    return None


__all__ = ["_head_carried_locally", "_phase_run_entries", "_published_head"]
