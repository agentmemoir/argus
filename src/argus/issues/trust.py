# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Allowlisted issue and comment authors, including local CODEOWNERS users."""

from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.issues.client import TOKEN_RE, IssueClient
from argus.issues.transport import IssuesUnavailable

ASSOCIATIONS = frozenset(
    {
        "COLLABORATOR",
        "CONTRIBUTOR",
        "FIRST_TIME_CONTRIBUTOR",
        "FIRST_TIMER",
        "MANNEQUIN",
        "MEMBER",
        "NONE",
        "OWNER",
        "UNKNOWN",
    }
)
DEFAULT_ASSOCIATIONS = ["OWNER", "MEMBER"]


class Trust:
    def __init__(self, repo: Path, client: IssueClient | None = None) -> None:
        self.authors = set(conventions.setting("issues.trusted-authors", []))
        self.apps = set(conventions.setting("issues.trusted-apps", []))
        self.associations = set(
            conventions.setting("issues.trusted-associations", DEFAULT_ASSOCIATIONS)
        )
        if not self.associations:
            raise IssuesUnavailable(
                "configuration", "issues.trusted-associations is empty"
            )
        self.owners: set[str] = set()
        self.unresolved: list[str] = []
        configured = conventions.setting("issues.codeowners-file", None)
        if configured is not None:
            path = Path(configured)
            path = path if path.is_absolute() else repo / path
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError) as exc:
                raise IssuesUnavailable(
                    "configuration", "issues.codeowners-file cannot be read"
                ) from exc
            teams: set[str] = set()
            for line in lines:
                tokens = line.split("#", 1)[0].split()
                for token in tokens[1:]:
                    if token.startswith("@") and "/" in token:
                        teams.add(token[1:])
                    elif token.startswith("@") and TOKEN_RE.fullmatch(token[1:]):
                        self.owners.add(token[1:].lower())
                    elif "@" in token:
                        self.unresolved.append(token)
            for team in sorted(teams):
                if conventions.setting("issues.resolve-teams", False):
                    if client is None:
                        raise IssuesUnavailable(
                            "configuration",
                            "issues.resolve-teams needs an issue source",
                        )
                    self.owners.update(x.lower() for x in client.team_members(team))
                else:
                    self.unresolved.append("@" + team)
        self.authors = {x.lower() for x in self.authors}
        self.apps = {x.lower() for x in self.apps}
        comment = conventions.setting("issues.comment-trust", {})
        self.comment_authors = {
            x.lower() for x in comment.get("authors", self.authors | self.owners)
        }
        self.comment_apps = {x.lower() for x in comment.get("apps", self.apps)}
        self.comment_associations = set(comment.get("associations", self.associations))

    def allows(self, item: dict[str, Any], *, comment: bool = False) -> bool:
        association = item.get("authorAssociation")
        if association not in ASSOCIATIONS:
            return False
        if not isinstance(item.get("author"), dict):
            return False
        author = cast("dict[str, Any]", item["author"])
        login = author.get("login")
        if not isinstance(login, str) or not TOKEN_RE.fullmatch(login):
            return False
        login = login.lower()
        apps = self.comment_apps if comment else self.apps
        associations = self.comment_associations if comment else self.associations
        authors = self.comment_authors if comment else self.authors | self.owners
        # A bot is trusted only through the apps allowlist: a trusted
        # association admits a person, not a bot.
        if author.get("is_bot") is True or author.get("type") == "Bot":
            return login in apps
        return association in associations or login in authors
