# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Structured GitHub reads via gh argv."""

import hashlib
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.issues.transport import IssuesUnavailable, run

REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
BRANCH_RE = re.compile(r"issue/([0-9]+)-[a-z0-9]+(?:-[a-z0-9]+)*")
TOKEN_RE = re.compile(r"[A-Za-z0-9_.:@/\[\]-]+\Z")
SEARCH_FIELDS = "number,title,labels,author,authorAssociation,createdAt,updatedAt,isPullRequest,assignees,url,state,commentsCount"
VIEW_FIELDS = (
    "number,title,body,labels,author,createdAt,updatedAt,comments,blockedBy,state"
)


def issue_number(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 9999999999
    ):
        raise IssuesUnavailable("invalid_response", "issue number")
    return value


def objects(value: Any, operation: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise IssuesUnavailable("invalid_response", operation)
    items = cast("list[object]", value)
    if any(not isinstance(item, dict) for item in items):
        raise IssuesUnavailable("invalid_response", operation)
    return cast("list[dict[str, Any]]", items)


def mapping(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IssuesUnavailable("invalid_response", operation)
    return cast("dict[str, Any]", value)


class IssueClient:
    def __init__(self) -> None:
        try:
            self.repo = conventions.setting("issues.repo")
            self.binary = str(conventions.setting("commands.gh"))
            self.timeout = conventions.setting("issues.timeout", 30)
        except ValueError as exc:
            raise IssuesUnavailable("configuration", str(exc)) from exc
        if not isinstance(self.repo, str) or not REPO_RE.fullmatch(self.repo):
            raise IssuesUnavailable("configuration", "issues.repo must be OWNER/NAME")

    def call(self, argv: list[str], *, parse: bool = True) -> Any:
        return run(self.binary, argv, self.timeout, parse=parse)

    def search(self, preference: str) -> list[dict[str, Any]]:
        argv = [
            "search",
            "issues",
            "--repo",
            self.repo,
            "--state",
            "open",
            "--limit",
            str(conventions.setting("issues.list-limit", 200)),
            "--sort",
            "best-match" if preference == "search" else "created",
            "--order",
            "desc",
            "--json",
            SEARCH_FIELDS,
        ]
        query = conventions.setting("issues.search", None)
        category = conventions.setting("issues.category", None)
        if preference == "category" and category:
            argv += ["--label", category]
        if query:
            argv += ["--", query]
        issues = objects(self.call(argv), "search issues")
        for issue in issues:
            issue_number(issue.get("number"))
            for key in (
                "title",
                "state",
                "authorAssociation",
                "createdAt",
                "updatedAt",
            ):
                if not isinstance(issue.get(key), str):
                    raise IssuesUnavailable("invalid_response", f"search issue {key}")
            if not isinstance(issue.get("author"), dict) or not isinstance(
                issue.get("isPullRequest"), bool
            ):
                raise IssuesUnavailable(
                    "invalid_response", "search issue author or kind"
                )
            objects(issue.get("labels"), "issue labels")
            if (
                not isinstance(issue.get("commentsCount"), int)
                or isinstance(issue.get("commentsCount"), bool)
                or issue["commentsCount"] < 0
            ):
                raise IssuesUnavailable("invalid_response", "comments count")
        return issues

    def argus_pull_requests(self) -> set[int]:
        """Issue numbers with an open pull request on an Argus issue branch."""
        limit = conventions.setting("issues.list-limit", 200)
        argv = ["pr", "list", "--repo", self.repo, "--state", "open"]
        argv += ["--limit", str(limit), "--json", "headRefName,isCrossRepository"]
        rows = objects(self.call(argv), "pr list")
        if len(rows) >= limit:
            # A full page may hide an Argus pull request, so no issue is free.
            raise IssuesUnavailable("truncated", "pr list")
        return {
            int(match.group(1))
            for row in rows
            if row.get("isCrossRepository") is False
            and (match := BRANCH_RE.fullmatch(str(row.get("headRefName"))))
        }

    def view(
        self, number: int, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        number = issue_number(number)
        value = mapping(
            self.call(
                [
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    self.repo,
                    "--json",
                    VIEW_FIELDS,
                ]
            ),
            "issue view",
        )
        if value.get("number") != number:
            raise IssuesUnavailable("invalid_response", "issue view")
        for key in ("title", "body", "state", "createdAt", "updatedAt"):
            if not isinstance(value.get(key), str):
                raise IssuesUnavailable("invalid_response", f"issue view {key}")
        objects(value.get("comments"), "issue comments")
        objects(value.get("labels"), "issue labels")
        author = mapping(value.get("author"), "issue author")
        blockers = mapping(value.get("blockedBy"), "issue dependencies")
        nodes = objects(blockers.get("nodes"), "issue dependencies")
        total = blockers.get("totalCount")
        if not isinstance(total, int) or isinstance(total, bool) or total < len(nodes):
            raise IssuesUnavailable("invalid_response", "issue dependency count")
        if any(n.get("state") not in {"OPEN", "CLOSED"} for n in nodes):
            raise IssuesUnavailable("invalid_response", "issue dependency state")
        # Search is the only CLI shape carrying association and total comments.
        merged = {**(metadata or {}), **value}
        if metadata:
            merged["authorAssociation"] = metadata["authorAssociation"]
            merged["commentsCount"] = metadata["commentsCount"]
            # A changed author must never inherit the previous author's trust.
            if author.get("login") != metadata["author"].get("login"):
                raise IssuesUnavailable("changed_author", "issue snapshot")
        return merged

    def comments(self, number: int) -> list[dict[str, Any]]:
        pages = self.call(
            [
                "api",
                f"repos/{self.repo}/issues/{issue_number(number)}/comments?per_page=100",
                "--paginate",
                "--slurp",
            ]
        )
        if not isinstance(pages, list):
            raise IssuesUnavailable("invalid_response", "comment pages")
        comments: list[dict[str, Any]] = []
        for page in cast("list[object]", pages):
            for c in objects(page, "comment page"):
                user = c.get("user")
                user = mapping({} if user is None else user, "comment author")
                comments.append(
                    {
                        **c,
                        "author": {**user, "is_bot": user.get("type") == "Bot"},
                        "authorAssociation": c.get("author_association"),
                        "createdAt": c.get("created_at"),
                    }
                )
        return comments

    def team_members(self, team: str) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", team):
            raise IssuesUnavailable("configuration", "CODEOWNERS team")
        org, slug = team.split("/")
        pages = self.call(
            ["api", f"orgs/{org}/teams/{slug}/members", "--paginate", "--slurp"]
        )
        if not isinstance(pages, list):
            raise IssuesUnavailable("invalid_response", "team pages")
        members = [
            m.get("login")
            for page in cast("list[object]", pages)
            for m in objects(page, "team members")
        ]
        if any(not isinstance(m, str) or not TOKEN_RE.fullmatch(m) for m in members):
            raise IssuesUnavailable("invalid_response", "team logins")
        return [str(member) for member in members]

    def comment(
        self,
        number: int,
        body_file: Path,
        *,
        report: bool = False,
        expected_sha256: str | None = None,
    ) -> None:
        number = issue_number(number)
        if report:
            try:
                raw = body_file.read_bytes()
            except OSError as exc:
                raise IssuesUnavailable(
                    "writeback_payload", "report payload cannot be read"
                ) from exc
            try:
                body = raw.decode("utf-8")
            except UnicodeError as exc:
                raise IssuesUnavailable(
                    "writeback_payload", "report must be UTF-8"
                ) from exc
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected_sha256:
                raise IssuesUnavailable(
                    "writeback_payload", "report payload changed after validation"
                )
            payload = (
                f"{body}\n\n<!-- argus-issue-report:issue={number}:sha256={digest} -->"
            )
            if any(
                c.get("body") == payload and c.get("id") for c in self.comments(number)
            ):
                return
            with tempfile.TemporaryDirectory(prefix="argus-issue-report-") as directory:
                path = Path(directory) / "report.md"
                path.write_text(payload, encoding="utf-8")
                self.comment(number, path)
            if not any(
                c.get("body") == payload and c.get("id") for c in self.comments(number)
            ):
                raise IssuesUnavailable(
                    "writeback_unconfirmed", "report comment has not been confirmed"
                )
            return
        self.call(
            [
                "issue",
                "comment",
                str(number),
                "--repo",
                self.repo,
                "--body-file",
                str(body_file),
            ],
            parse=False,
        )

    def add_label(self, number: int, label: str) -> None:
        """Add one label to an issue; create it first when the repo lacks it."""
        number = issue_number(number)
        listed = self.call(
            ["label", "list", "--repo", self.repo, "--search", label]
            + ["--limit", "100", "--json", "name"]
        )
        names = {
            str(item.get("name")).casefold() for item in objects(listed, "label list")
        }
        if label.casefold() not in names:
            self.call(["label", "create", label, "--repo", self.repo], parse=False)
            print(f"issue claim: created label {label!r}", file=sys.stderr)
        self.call(
            ["issue", "edit", str(number), "--repo", self.repo, "--add-label", label],
            parse=False,
        )
