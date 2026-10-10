# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The group checklist checks: per-push comment, body tick, final tidy."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.gh import (
    gh_comment_pull_request,
    gh_edit_pull_request_body,
    gh_pull_request_body,
)
from argus.gitsync.group_review import (
    _group_task_labels,
    _tidy_group_body,
    _update_body_checklist,
    update_group_pull_request,
)
from argus.gitsync.selftest_fixture import BOARD_REL, _StubGH
from argus.gitsync.selftest_group_review import _receipt_fixture, _write_receipt


def _body_primer_checks(results: list[tuple[str, object, str]]) -> None:
    with _StubGH({"stdout": json.dumps({"body": "hello\n- [ ] task"})}):
        body, _detail = gh_pull_request_body(7, "example-org", "example-repo")
    results.append(
        (
            "a pull request body reads through gh",
            body == "hello\n- [ ] task",
            f"body={body!r}",
        )
    )
    with _StubGH({"returncode": 1}):
        body, _detail = gh_pull_request_body(7, "example-org", "example-repo")
    results.append(
        (
            "an unreadable pull request body reads None, not empty",
            body is None,
            f"body={body!r}",
        )
    )
    with _StubGH({"returncode": 0, "stdout": ""}):
        ok, _detail = gh_edit_pull_request_body(
            7, "example-org", "example-repo", None, "new"
        )
    results.append(("a body edit exiting 0 reports ok", ok, f"ok={ok}"))
    with _StubGH({"returncode": 1}):
        ok, _detail = gh_edit_pull_request_body(
            7, "example-org", "example-repo", None, "new"
        )
    results.append(("a refused body edit is reported, not raised", not ok, f"ok={ok}"))
    with _StubGH({"returncode": 0, "stdout": ""}):
        ok, _detail = gh_comment_pull_request(
            7, "example-org", "example-repo", None, "done"
        )
    results.append(("a comment exiting 0 reports ok", ok, f"ok={ok}"))
    with _StubGH({"returncode": 1}):
        ok, _detail = gh_comment_pull_request(
            7, "example-org", "example-repo", None, "done"
        )
    results.append(("a refused comment is reported, not raised", not ok, f"ok={ok}"))


def _checklist_checks(base: Path, results: list[tuple[str, object, str]]) -> None:
    new_body, changed = _update_body_checklist(
        "Plan\n- [ ] cache warm\n- [ ] index build\n",
        [("cache warm", True), ("index build", False)],
    )
    results.append(
        (
            "a completed checkbox ticks while the pending one stays open",
            changed
            and "- [x] cache warm" in new_body
            and "- [ ] index build" in new_body,
            f"body={new_body!r}",
        )
    )
    new_body2, _changed2 = _update_body_checklist(new_body, [("cache warm", True)])
    results.append(
        (
            "a ticked checkbox never unticks",
            "- [x] cache warm" in new_body2,
            f"body={new_body2!r}",
        )
    )
    tidy = _tidy_group_body("title   \n\n\n\n- [x] done   \n")
    results.append(
        (
            "final tidy strips trailing space and tall gaps",
            tidy == "title\n\n\n- [x] done\n",
            f"tidy={tidy!r}",
        )
    )
    pub = _receipt_fixture(base, "group-checklist")
    roadmap = pub / BOARD_REL / "roadmap"
    roadmap.mkdir(parents=True, exist_ok=True)
    (roadmap / "phase-100020-cache-warm.md").write_text("# 20\n", encoding="utf-8")
    (roadmap / "phase-100030-index-build.md").write_text("# 30\n", encoding="utf-8")
    labels = _group_task_labels(pub, ["100020", "100030"])
    results.append(
        (
            "task labels carry slugs and never phase numbers",
            [label for _key, label in labels] == ["cache warm", "index build"]
            and all("10002" not in label for _key, label in labels),
            f"labels={labels}",
        )
    )
    url = "https://github.com/example-org/example-repo/pull/7"
    _write_receipt(
        pub,
        100020,
        state="awaiting_review",
        branch="b",
        group=["100020", "100030"],
        pull_request=url,
        public_head="abc1234def",
    )
    _write_receipt(
        pub,
        100030,
        state="awaiting_review",
        branch="b",
        group=["100020", "100030"],
        pull_request=url,
        public_head="def5678abc",
    )
    calls: list[Any] = []
    saved = subprocess.run

    def _route(argv: Any, **kwargs: Any) -> Any:
        import types

        if not argv or Path(argv[0]).name != "gh":
            return saved(argv, **kwargs)
        calls.append(argv)
        text = " ".join(str(part) for part in argv)
        if "comment" in text:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--json" in text:
            return types.SimpleNamespace(
                stdout=json.dumps(
                    {"body": "Plan\n- [ ] cache warm\n- [ ] index build\n"}
                ),
                returncode=0,
                stderr="",
            )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    subprocess.run = _route  # type: ignore[assignment]
    try:
        updated = update_group_pull_request(pub, 100030, ["100020", "100030"], url)
    finally:
        subprocess.run = saved
    results.append(
        (
            "each publish posts a comment and ticks its own box",
            isinstance(updated, dict)
            and updated.get("comment_ok") is True
            and updated.get("body_ok") is True
            and any("comment" in " ".join(str(p) for p in argv) for argv in calls),
            f"updated={updated}",
        )
    )


def _group_checklist_checks(base: Path) -> list[tuple[str, object, str]]:
    results: list[tuple[str, object, str]] = []
    _body_primer_checks(results)
    _checklist_checks(base, results)
    return results


__all__ = ["_group_checklist_checks"]
