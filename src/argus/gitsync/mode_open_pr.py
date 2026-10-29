# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One draft pull request for a branch: create it, or reuse the one that exists.

A phase pipeline asks for this mode with no arguments but the branch it already knows, so every default here must work with no extra flags: reusing an open pull request takes no body file, and only a create needs ``--body-file``.

Three outcomes, and the mode keeps them apart:

* no pull request for the branch: create a draft, then read that pull request
  back and report the number, URL, draft flag, and state the read returns. The
  create prints a URL and nothing else, so the rest is not known until the read.
* an open pull request for the branch: reuse it. The pipeline calls this on
  every tick, so a second call must never open a second draft for the same
  commits.
* the newest pull request for the branch is merged or closed: refuse with
  ``state: BLOCKED`` and name that pull request. New work on a branch whose
  pull request already closed is the reviewer's decision, not this mode's.

``state`` is upper case, like every other gitsync mode, so one reader switches
on the same key across all of them. A created or reused pull request reports
the state gh gave for it, which is ``OPEN``; a refusal reports ``BLOCKED``.

A gh read that fails is never read as "no pull request". Believing that would
create a duplicate on every network blip, so an unreadable list stops the mode.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus import conventions
from argus.gitsync.fence import phase_fence
from argus.gitsync.gh import gh_create_draft_pull_request, gh_pull_requests_for_branch
from argus.gitsync.gh_exit import gh_exit_detail
from argus.gitsync.git import ConfigError, current_branch, git
from argus.gitsync.open_pr_base import resolve_base
from argus.gitsync.open_pr_ready import fork_refusal, text_from_file, unpushed_refusal
from argus.gitsync.paths import phase_branch
from argus.issues.pr_record import record_pull_request


def resolve_branch(root: Path, phase: int | str | None, branch: str | None) -> str:
    """The branch to open the pull request from.

    An explicit branch wins; then the phase's own branch, derived from its phases
    file; then the branch the checkout is on. Nothing is guessed: a phase with no
    phases file, a checkout on no branch, or a checkout with no commit yet is a
    configuration error naming what is missing, so a wrong call fails in seconds.

    Raises:
        ConfigError: No branch could be resolved.
    """
    if branch:
        return str(branch)
    if phase is not None:
        number = int(phase)
        found = conventions.resolve_phase_file(root, number)
        if found is None:
            raise ConfigError(
                f"--phase {number:06d} has no phases file in the configured "
                "phases folder; pass --branch"
            )
        return phase_branch(found.name)
    here = current_branch(root)
    if not here:
        # An empty answer means one of two things, and they need different ways
        # out: a checkout on no branch at all, or a checkout with no commit yet.
        rc, _, _ = git(root, "rev-parse", "--verify", "HEAD")
        if rc != 0:
            raise ConfigError(
                f"{root} has no commit yet, so there is no branch to open a pull "
                "request from; make a commit, or pass --branch"
            )
        raise ConfigError(
            f"{root} is on a detached HEAD, so there is no branch to open a pull "
            "request from; check out the branch, or pass --branch or --phase"
        )
    return here


def _local_branch_missing(root: Path, branch: str) -> str | None:
    """Why ``branch`` is not a local branch here, or None when it is one.

    A typo would otherwise reach gh and come back as a create failure with no
    way out in it, so the branch is checked before gh is asked anything. The
    check holds for every source of the branch: an explicit ``--branch``, a
    phase's own branch, and the branch the checkout is on. A phase whose branch
    was never created is the case that reached gh and returned a bare failure.

    One message for every source, naming the branch and the checkout, because a
    reader should not have to learn two wordings to answer one question.
    """
    rc, _, _ = git(root, "rev-parse", "--verify", f"refs/heads/{branch}")
    if rc == 0:
        return None
    return (
        f"the branch {branch!r} is not a branch in {root}; create it, or pass "
        "--branch for a branch this checkout has"
    )


def _blocked(branch: str, row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    """The mode's result for one blocked row, with the reasons recorded on it."""
    row["blockers"] = reasons
    return _result(branch, [row])


def _draft_or_reason(item: dict[str, Any]) -> tuple[bool | None, str | None]:
    """The draft flag the read gave, or None and why it cannot be reported.

    A missing flag is not ``false``, and the string ``"false"`` is not ``true``.
    The mode reports the draft state it was asked for, so an unknown one is a
    blocked result and never a default.
    """
    draft = item.get("isDraft")
    if isinstance(draft, bool):
        return draft, None
    number = int(item["number"])
    return None, (
        f"gh did not report the draft flag for #{number} as true or false, so "
        "its draft state is unknown; a default is not a state"
    )


def _draft_word(draft: object) -> str:
    """The draft state in words, and ``unknown`` when gh did not report one."""
    if isinstance(draft, bool):
        return "draft" if draft else "not draft"
    return "draft unknown"


def _summary(rows: list[dict[str, Any]]) -> str:
    """One line naming the pull request: its number, action, and draft state.

    A reader of the record must be able to tell #11 from #12 and read the draft
    state without opening the JSON row, so both are in this line. A blocked row
    names its reasons instead.
    """
    parts: list[str] = []
    for row in rows:
        if row.get("ok"):
            parts.append(
                f"{row['repo']}: #{int(row['number'])} {row['action']}, "
                f"{_draft_word(row['draft'])}"
            )
        else:
            parts.append(
                f"{row['repo']}: BLOCKED ({'; '.join(row.get('blockers', []))})"
            )
    head = f"open-pr sync {'ok' if all(row.get('ok') for row in rows) else 'BLOCKED'}"
    return f"{head}: " + "; ".join(parts)


def _result(branch: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The mode's result, in the shape every other mode returns.

    ``state`` is the pull request state gh reported for the row, so a created or
    reused pull request reports ``OPEN``, and ``BLOCKED`` when the mode could not
    finish. It is upper case for the same reason every other mode's is: a record
    reader switches on one key and one vocabulary across all of them.
    """
    ok = bool(rows) and all(bool(row.get("ok")) for row in rows)
    state = str(rows[0].get("state")) if ok else "BLOCKED"
    return {
        "ok": ok,
        "mode": "open-pr",
        "state": state,
        "branch": branch,
        "repos": rows,
        "summary": _summary(rows),
    }


def _row(root: Path) -> dict[str, Any]:
    """One repository's row, with every field present and unclaimed."""
    return {
        "repo": str(root),
        "ok": False,
        "action": None,
        "number": None,
        "url": None,
        "draft": None,
        "state": None,
    }


def mode_open_pr(
    root: str | Path,
    phase: int | str | None = None,
    branch: str | None = None,
    title_file: str | Path | None = None,
    body_file: str | Path | None = None,
    base: str | None = None,
    before_create: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Open a draft pull request for a branch, or reuse the one that exists.

    ``before_create`` runs after the pull request list is read and just before the create. A caller that holds a claim passes a re-read of it, so a claim lost during the read creates nothing.

    The title defaults to the branch name. ``--body-file`` is required only when a draft has to be created: a draft opened with an empty body can be published before a later pass fills it, and a reused pull request is never written to, so the reuse path needs no body file. ``title_file`` and ``body_file`` let a caller supply its own text, and a file that is missing or empty is refused rather than quietly replaced by the default.

    ``base`` names the branch to open into. When it is not given, the base is
    resolved from the checkout and then from gh, and a base neither can name is
    a refusal naming ``--base`` rather than a guess.

    Returns the mode's result dict: ``ok``, ``mode``, ``state``, ``branch``,
    ``repos``, and ``summary``, with ``state: BLOCKED`` and a blocker naming the
    reason when the mode could not finish.
    """
    root = Path(root).resolve()
    branch_name = resolve_branch(root, phase, branch)
    # A phase's pull request is a publication: the board fence is read before the
    # checkout, the pull request list, or the create is touched.
    if phase is not None:
        fenced = phase_fence(root, phase, "open-pr")
        if fenced is not None:
            return fenced
    # Every source of the branch is checked against the checkout, not only an
    # explicit --branch. A phase whose branch was never created used to reach
    # gh and come back as a bare failure that named nothing.
    missing = _local_branch_missing(root, branch_name)
    if missing is not None:
        raise ConfigError(missing)
    title = text_from_file(title_file, "--title-file")
    body = text_from_file(body_file, "--body-file")
    # A fork checkout asks about the wrong repository, and an unpushed branch
    # comes back from gh as a bare exit. Both are refused with a way out.
    for refusal in (fork_refusal(root), unpushed_refusal(root, branch_name)):
        if refusal is not None:
            raise ConfigError(refusal)
    row = _row(root)

    found, why = gh_pull_requests_for_branch(root, branch_name)
    if found is None:
        # Could not read. Not the same as "no pull request exists", so nothing is
        # created on a guess.
        return _blocked(
            branch_name,
            row,
            [f"could not list pull requests for {branch_name!r}: {why}"],
        )

    open_rows = [item for item in found if item["state"] == "OPEN"]
    if open_rows:
        newest = max(open_rows, key=lambda item: int(item["number"]))
        draft, unknown = _draft_or_reason(newest)
        row.update(
            number=int(newest["number"]),
            url=newest["url"],
            draft=draft,
            state="OPEN",
        )
        if unknown is not None:
            return _blocked(branch_name, row, [unknown])
        row.update(ok=True, action="reused")
        return _result(branch_name, [row])

    if found:
        newest = max(found, key=lambda item: int(item["number"]))
        state = str(newest["state"])
        draft, unknown = _draft_or_reason(newest)
        row.update(
            number=int(newest["number"]),
            url=newest["url"],
            draft=draft,
            state=state,
        )
        reasons = [
            f"the newest pull request for {branch_name!r} is {state}, "
            f"#{int(newest['number'])} {newest['url']}; not opening a duplicate"
        ]
        if unknown is not None:
            reasons.append(unknown)
        return _blocked(branch_name, row, reasons)

    # Only the create sends the body, so the refusal for a missing one sits here.
    if body is None:
        raise ConfigError(
            "--body-file is required for --mode open-pr: a draft pull request is "
            "never opened with an empty body; pass the pull request prose in a file"
        )
    if before_create is not None:
        before_create()
    created, detail = gh_create_draft_pull_request(
        root,
        branch_name,
        resolve_base(root, base),
        title=title,
        body=body,
    )
    if created is None:
        return _blocked(
            branch_name,
            row,
            [f"gh could not create a draft pull request for {branch_name!r}: {detail}"],
        )
    url = created.strip()
    if not url:
        # gh exited 0 and printed nothing. A create that named no pull request is
        # not a created one, and the number cannot be borrowed from anywhere.
        return _blocked(
            branch_name,
            row,
            [
                f"{gh_exit_detail(0, 'the create')} for {branch_name!r} but printed "
                "no pull request URL, "
                "so no pull request is confirmed; check the branch by hand"
            ],
        )
    # Record the pull request on the issue's claim now. The tick writes it too, if this fails.
    record_pull_request(root, branch_name, url)
    # gh printed the new pull request's URL. That URL is all it reports, so the
    # number, draft flag, and state are read back from the pull request itself.
    again, why = gh_pull_requests_for_branch(root, branch_name)
    if again is None:
        return _blocked(
            branch_name,
            row,
            [
                f"gh created {url} for {branch_name!r} but the pull request could "
                f"not be read back: {why}; its number, draft flag, and state are "
                "unknown"
            ],
        )
    matched = [item for item in again if item["url"] == url]
    if not matched:
        return _blocked(
            branch_name,
            row,
            [
                f"gh printed {url} for {branch_name!r}, but the read of that branch "
                "does not list it; the create is unconfirmed"
            ],
        )
    newest = max(matched, key=lambda item: int(item["number"]))
    draft, unknown = _draft_or_reason(newest)
    row.update(
        number=int(newest["number"]),
        url=str(newest["url"]),
        draft=draft,
        state=str(newest["state"]),
    )
    if unknown is not None:
        return _blocked(branch_name, row, [unknown])
    row.update(ok=True, action="created")
    return _result(branch_name, [row])


__all__ = ["mode_open_pr", "resolve_branch"]
