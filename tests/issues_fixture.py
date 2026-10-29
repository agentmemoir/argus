# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue fixtures exercise the external gh boundary without a token."""

import atexit
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.board.reservations import ReservationConflict
from argus.exec_tempdir import exec_tempdir

SENTINEL = "REMOTE_TEXT_SENTINEL"
GROUPS = {
    "type": {"labels": ["bug", "feature"], "cardinality": "one"},
    "area": {"labels": ["area:cli", "area:store"], "cardinality": "many"},
    "difficulty": {
        "labels": ["difficulty:easy", "difficulty:hard"],
        "cardinality": "one",
    },
}


def issue(number: int = 100001, **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Fix the input",
        "body": SENTINEL,
        "author": {"login": "maintainer", "is_bot": False},
        "authorAssociation": "OWNER",
        "state": "OPEN",
        "isPullRequest": False,
        "labels": [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")],
        "createdAt": "2026-01-01T12:00:00Z",
        "updatedAt": "2026-01-02T12:00:00Z",
        "comments": [],
        "commentsCount": 0,
        "assignees": [],
        "closedByPullRequestsReferences": [],
        "blockedBy": {"nodes": [], "totalCount": 0},
        "url": f"https://github.com/example/project/issues/{number}",
        **extra,
    }


def settings(repo: Path, binary: Path) -> dict[str, Any]:
    return {
        "repo-root": repo,
        "machine.id": "worker-one",
        "pipeline": None,
        "phases.directory": repo,
        "driver.runs": repo / "runs",
        "commands.gh": str(binary),
        "commands.git": "git",
        "board.repo": repo / "board",
        "board.file": Path("phases.json"),
        "board.issues-file": Path("issues.json"),
        "issues.repo": "example/project",
        "issues.preference": "newest",
        "issues.label-groups": GROUPS,
        "issues.difficulty-order": ["difficulty:easy", "difficulty:hard"],
        "issues.task-dir": repo / "tasks",
        "issues.claim-label": "accepted",
    }


def fake_gh(
    repo: Path,
    rows: list[dict[str, Any]],
    *,
    add_cleanup: Callable[[Callable[[], None]], object] | None = None,
) -> Path:
    fixture = repo / "github.json"
    fixture.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    executable = exec_tempdir(purpose="the issue fixture's fake gh")
    (add_cleanup or atexit.register)(executable.cleanup)
    binary = Path(executable.name) / "gh"
    binary.write_text(
        f"#!{sys.executable}\n"
        + f"FIXTURE_ROOT = {str(repo)!r}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(FIXTURE_ROOT)
args = sys.argv[1:]
with (root / "argv.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": args, "remote_in_env": any("REMOTE_TEXT_SENTINEL" in value for value in os.environ.values())}) + "\\n")
fixture = json.loads((root / "github.json").read_text())
mode = fixture.get("mode")
if mode == "timeout":
    time.sleep(10)
if mode == "failure":
    print(fixture.get("error", "failed"), file=sys.stderr)
    sys.exit(fixture.get("exit", 1))
if mode == "empty":
    sys.exit(0)
if mode == "invalid":
    print("bad-json")
    sys.exit(0)
if mode == "edit-failure" and args[:2] == ["issue", "edit"]:
    print("permission denied", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["search", "issues"]:
    result = fixture["rows"]
elif args[:2] == ["issue", "view"]:
    result = next(row for row in fixture["rows"] if row["number"] == int(args[2]))
elif args[:2] == ["pr", "view"]:
    result = {"state": fixture.get("pr_states", {}).get(args[2], "CLOSED")}
elif args[:2] == ["pr", "list"]:
    if fixture.get("pr_list") == "failure":
        sys.exit(1)
    head = args[args.index("--head") + 1]
    result = [row for row in fixture.get("branch_pulls", []) if row.get("headRefName") == head]
elif args[:2] == ["pr", "checks"]:
    if fixture.get("checks_error"):
        sys.exit(1)
    result = fixture.get("checks", {}).get(args[2], [])
elif args[:2] == ["label", "list"]:
    result = [{"name": name} for name in fixture.get("labels", [])]
elif args[:2] == ["label", "create"]:
    fixture.setdefault("labels", []).append(args[2])
    (root / "github.json").write_text(json.dumps(fixture))
    sys.exit(0)
elif args[0] == "api" and "/pulls?" in args[-1]:
    result = fixture.get("pull_pages", [fixture.get("pulls", [])])
elif args[0] == "api":
    result = fixture.get("pages", [[]])
elif args[:2] == ["issue", "comment"]:
    body = Path(args[args.index("--body-file") + 1]).read_text()
    fixture.setdefault("pages", [[]])[0].append({"id": 1, "body": body, "user": {"login": "maintainer"}, "author_association": "OWNER", "created_at": "2026-01-01T12:00:00Z"})
    (root / "github.json").write_text(json.dumps(fixture))
    sys.exit(0)
else:
    sys.exit(0)
print(json.dumps(result))
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def fixture_update(repo: Path, **extra: Any) -> None:
    path = repo / "github.json"
    path.write_text(
        json.dumps({**json.loads(path.read_text()), **extra}), encoding="utf-8"
    )


def argv(repo: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (repo / "argv.jsonl").read_text().splitlines()]


class Board:
    def __init__(
        self, records: dict[str, Any] | None = None, losses: set[int] | None = None
    ) -> None:
        self.records: dict[str, Any] = records or {}
        self.losses = losses or set()
        self.claims: list[int] = []
        self.released: list[str] = []

    def read(self) -> tuple[dict[str, Any], str]:
        return {"phases": self.records}, "fixture-revision"

    def reserve(self, number: int, machine: str, *, run_id: str) -> dict[str, Any]:
        self.claims.append(number)
        if number in self.losses:
            raise ReservationConflict(
                number, {"machine_id": "other-worker", "status": "claimed"}
            )
        record = {
            "issue": str(number),
            "machine_id": machine,
            "status": "claimed",
            "reservation_id": "rsv-fixture",
            "generation": 1,
            "run_id": run_id,
        }
        self.records[f"{number:06d}"] = record
        return record

    def release_group(self, reservation: dict[str, Any]) -> None:
        key = f"{int(reservation['issue']):06d}"
        self.records.pop(key, None)
        self.released.append(key)

    def set_issue_receipt(
        self, reservation: dict[str, Any], receipt: dict[str, Any] | None
    ) -> None:
        record = self.records[f"{int(reservation['issue']):06d}"]
        if receipt is None:
            record.pop("completion_receipt", None)
        else:
            record["completion_receipt"] = receipt

    def assert_owner(self, reservation: dict[str, Any]) -> dict[str, Any]:
        return self.records[f"{int(reservation['issue']):06d}"]

    def expire_stale(self) -> list[dict[str, Any]]:
        return []
