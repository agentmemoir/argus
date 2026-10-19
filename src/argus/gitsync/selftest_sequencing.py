# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The reliable-sequencing checks for pull request references and merges."""

import json
import subprocess
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.gitsync.branch import ensure_phase_branch
from argus.gitsync.gh import gh_pull_request_state
from argus.gitsync.git import GH_TIMEOUT_S, git, integration_branch
from argus.gitsync.publish import mark_phase_published
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.receipts import (
    phase_receipt,
    pull_request_ref,
)
from argus.gitsync.selftest_fixture import _gh_says, _StubGH
from argus.gitsync.selftest_publish import _publish_fixture
from argus.gitsync.selftest_stacked import (
    _sequencing_integration_checks,
    _sequencing_stack_checks,
)


def _sequencing_checks(base: Path) -> list[tuple[str, object, str]]:
    """Items 1, 2, 3, 5 and 7 of the reliable-sequencing plan.

    Every merge-detection case here is measured against a real squash, because
    the check this replaces passed a single-commit test and failed on a
    multi-commit one. That is why the fixtures commit several times rather than
    once: a one-commit phase is not the case that occurs.
    """
    results: list[tuple[str, object, str]] = []
    old_branch: str = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        _sequencing_reference_checks(results)
        _sequencing_merge_checks(base, results)
        _sequencing_integration_checks(base, results)
        _sequencing_stack_checks(base, results)
    except Exception as exc:  # noqa: BLE001 - the self-test reports the failure
        results.append(
            ("reliable-sequencing checks ran to completion", False, repr(exc))
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _sequencing_reference_checks(
    results: list[tuple[str, object, str]],
) -> None:
    # --- Item 2: the pull request reference is parsed at receipt time. ---
    parsed = [
        (
            "https://github.com/example-org/example-repo/pull/42",
            ("github.com", "example-org", "example-repo", 42),
        ),
        (
            "https://github.com/example-org/example-repo/pull/42/files",
            ("github.com", "example-org", "example-repo", 42),
        ),
        (
            "https://github.com/example-org/example-repo/pull/42#issuecomment-1",
            ("github.com", "example-org", "example-repo", 42),
        ),
        (
            "http://ghe.example.com/example-org/example-repo/pull/7",
            ("ghe.example.com", "example-org", "example-repo", 7),
        ),
    ]
    wrong: list[tuple[str | None, object, object]] = []
    for url, want in parsed:
        ref = pull_request_ref(url)
        got = (ref["host"], ref["owner"], ref["repo"], ref["number"]) if ref else None
        if got != want:
            wrong.append((url, got, want))
    # The negative direction matters more: a string that is not a pull
    # request URL must yield None so the check is skipped, never a number
    # guessed from the wrong field.
    for candidate in (
        "",
        "not a url",
        "https://github.com/example-org/example-repo/issues/42",
        "https://github.com/example-org/example-repo/pull/abc",
        None,
    ):
        if pull_request_ref(candidate) is not None:
            wrong.append((candidate, "parsed", None))
    results.append(
        (
            "a pull request URL yields owner, repo and number; anything else "
            "yields no reference",
            not wrong,
            f"mismatches={wrong}",
        )
    )

    # --- Item 1: three answers, and unknown is never merged. ---
    with _StubGH(_gh_says("MERGED", "2026-01-01T00:00:00Z")):
        state, detail = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        (
            "gh reporting MERGED is the merged answer",
            state == "merged" and detail == "0" * 40,
            f"state={state} detail={detail}",
        )
    )
    with _StubGH(_gh_says("OPEN")):
        state, _ = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        (
            "gh reporting OPEN is the not-merged answer",
            state == "not-merged",
            f"state={state}",
        )
    )
    # Exit 4 is gh's documented "authentication required", exit 1 a general
    # failure. Both must read unknown, or a token that cannot read pull
    # requests would look like a phase that was never merged... which is
    # the safe direction but silently disables the fix, so it is pinned.
    for code in (1, 4):
        with _StubGH({"returncode": code, "stderr": "account name here"}):
            state, detail = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                f"gh exiting {code} is unknown, not a merge",
                state == "unknown" and "account name" not in detail,
                f"state={state} detail={detail}",
            )
        )
    with _StubGH(FileNotFoundError()):
        state, _ = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        ("gh missing is unknown, not a merge", state == "unknown", f"state={state}")
    )
    with _StubGH(subprocess.TimeoutExpired(cmd="gh", timeout=GH_TIMEOUT_S)):
        state, _ = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        (
            "gh timing out is unknown, not a merge",
            state == "unknown",
            f"state={state}",
        )
    )
    with _StubGH({"stdout": "not json"}):
        state, _ = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        (
            "unreadable gh output is unknown, not a merge",
            state == "unknown",
            f"state={state}",
        )
    )
    with _StubGH({"stdout": json.dumps({"state": "WHATEVER"})}):
        state, _ = gh_pull_request_state(42, "example-org", "example-repo")
    results.append(
        (
            "an unrecognised gh state is unknown, not a merge",
            state == "unknown",
            f"state={state}",
        )
    )


def _sequencing_merge_checks(
    base: Path, results: list[tuple[str, object, str]]
) -> None:
    # --- Item 1 end to end: a squash merge, recognised by GitHub. ---
    # Five commits, squashed into one, which is the case the commit-id test
    # alone fails. This is the deadlock the plan exists to remove.
    pub, nested, _run = _publish_fixture(base, "squash-github", 100451)
    branch_a = "feature/merge-detect-squash"
    ensure_phase_branch(pub, branch_a, base="main")
    for step in range(5):
        (pub / f"part-{step}.txt").write_text(f"phase work {step}\n")
        git(pub, "add", f"part-{step}.txt")
        git(pub, "commit", "-q", "-m", f"phase 100451 step {step}")
    res = publish_phase(
        pub,
        100451,
        pull_request="https://github.com/example-org/example-repo/pull/900",
    )
    receipt: dict[str, Any] = res.get("receipt") or {}
    ref = receipt.get("pull_request_ref")
    results.append(
        (
            "the receipt records the parsed pull request alongside the URL",
            res.get("ok")
            and ref
            == {
                "host": "github.com",
                "owner": "example-org",
                "repo": "example-repo",
                "number": 900,
            },
            f"ref={ref}",
        )
    )
    # The human merges onto whichever branch this repository actually calls
    # its integration branch, read the same way the receipt read it. Naming
    # "main" here instead would merge into a branch the receipt never
    # mentions, and the check would refuse for the right reason and the
    # wrong one at the same time.
    integration = integration_branch(pub)
    git(pub, "checkout", "-q", integration)
    git(pub, "merge", "-q", "--squash", branch_a)
    git(pub, "commit", "-q", "-m", "phase 100451 (squashed by a human)")
    git(pub, "push", "-q", "origin", integration)
    private_integration = integration_branch(nested)
    git(nested, "checkout", "-q", private_integration)
    git(nested, "merge", "-q", "--no-edit", branch_a)
    git(nested, "push", "-q", "origin", private_integration)

    # The phase is recorded published once its commit, push, and pull request
    # succeeded. No merge is required and no commit-id or receipt check gates it.
    marked = mark_phase_published(pub, 100451)
    published = cast("dict[str, Any]", phase_receipt(pub, 100451))
    results.append(
        (
            "a phase is recorded published without requiring a merge",
            marked["ok"]
            and marked.get("action") == "marked-published"
            and published.get("state") == "published"
            and published.get("merge_confirmed_by")
            == {"public": "recorded", "private": "recorded"},
            f"ok={marked.get('ok')} action={marked.get('action')} "
            f"receipt={published.get('merge_confirmed_by')}",
        )
    )
    again = mark_phase_published(pub, 100451)
    results.append(
        (
            "recording the same phase again is a no-op",
            again["ok"] and again.get("action") == "already-published",
            f"action={again.get('action')}",
        )
    )

    # --- The same result when GitHub cannot be asked. ---
    pub2, _nested2, _run2 = _publish_fixture(base, "squash-offsite", 100452)
    branch_b = "feature/merge-detect-offsite"
    ensure_phase_branch(pub2, branch_b, base="main")
    (pub2 / "part.txt").write_text("work\n")
    git(pub2, "add", "part.txt")
    git(pub2, "commit", "-q", "-m", "phase 100452")
    publish_phase(pub2, 100452, pull_request="https://example.test/pr/1")
    with _StubGH({"returncode": 1}):
        offsite = mark_phase_published(pub2, 100452)
    offsite_receipt = cast("dict[str, Any]", phase_receipt(pub2, 100452))
    results.append(
        (
            "a phase publishes the same way when GitHub cannot be asked",
            offsite["ok"]
            and offsite.get("action") == "marked-published"
            and offsite_receipt.get("state") == "published",
            f"ok={offsite.get('ok')} action={offsite.get('action')} "
            f"state={offsite_receipt.get('state')}",
        )
    )


__all__ = ["_sequencing_checks"]
