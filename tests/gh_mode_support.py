# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The gh stub the open-pr and peek-ci modes are tested against.

One stub, one checkout, and one transport file, so both mode files read the
same way. The stub lives in the ``exec_dir`` fixture, which is a folder the host
runs files from; only its data goes to ``tmp_path``.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync.git import git

BRANCH = "feature/cache-warm"
URL = "https://github.com/example/argus/pull/11"
# What the read after a create reports for the new pull request.
CREATED = {"number": 11, "state": "OPEN", "isDraft": True, "url": URL}

STUB_OPEN_PR = """#!__PYTHON__
import json
import sys
from pathlib import Path

transport = json.loads(Path(__TRANSPORT__).read_text())
call = sys.argv[1:]
log = Path(__CALLS__)
seen = log.read_text().splitlines() if log.exists() else []
reads = sum(1 for item in seen if json.loads(item)[:2] == ["pr", "list"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(call) + "\\n")
if call[:2] == ["pr", "list"]:
    later = transport.get("reread")
    print(json.dumps(later if reads and later is not None else (transport.get("list") or [])))
    raise SystemExit(int(transport.get("list_exit", 0)))
elif call[:2] == ["pr", "create"]:
    print(transport.get("create") or "")
    raise SystemExit(int(transport.get("create_exit", 0)))
else:
    raise SystemExit(3)
"""


STUB_PEEK_CI = """#!__PYTHON__
import json
import sys
from pathlib import Path

transport = json.loads(Path(__TRANSPORT__).read_text())
call = sys.argv[1:]
with Path(__CALLS__).open("a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")
if call[:2] == ["pr", "list"]:
    print(json.dumps(transport.get("list") or []))
elif call[:2] == ["pr", "checks"]:
    rows = transport.get("checks")
    if rows is not None:
        print(json.dumps(rows))
    raise SystemExit(int(transport.get("checks_exit") or 0))
else:
    raise SystemExit(3)
"""


@dataclass
class Workspace:
    """A checkout, the phases folder beside it, and a stubbed gh."""

    root: Path
    phases: Path
    transport: Path
    calls: Path
    defaults: dict[str, object] = field(default_factory=dict)

    def answer(self, **data: object) -> None:
        """Set what the stub gh answers next, over the file's own defaults."""
        self.transport.write_text(json.dumps({**self.defaults, **data}))

    def commands(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(row) for row in self.calls.read_text().splitlines()]

    def check_calls(self) -> list[list[str]]:
        return [call for call in self.commands() if call[:2] == ["pr", "checks"]]


def _keep_settings(_args: object) -> None:
    return None


def build_workspace(
    tmp_path: Path,
    exec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stub: str,
    defaults: dict[str, object],
    branch: str | None,
) -> Workspace:
    """A git checkout with a stub gh, wired into the settings the modes read."""
    root = tmp_path / "public"
    root.mkdir()
    phases = root / "phases"
    phases.mkdir()
    # The stub lives where the host really runs a file; only its data is temp.
    path = exec_dir / "gh"
    transport = tmp_path / "transport.json"
    calls = tmp_path / "calls.jsonl"
    transport.write_text(json.dumps(defaults))
    path.write_text(
        stub.replace("__PYTHON__", sys.executable)
        .replace("__TRANSPORT__", repr(str(transport)))
        .replace("__CALLS__", repr(str(calls)))
    )
    path.chmod(0o755)
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "commands.git": "git",
            "commands.gh": str(path),
            "repo-root": str(root),
            "phases.directory": str(phases),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
        },
    )
    assert git(root, "init", "-q", "-b", "main")[0] == 0
    (root / "product.txt").write_text("work\n")
    assert git(root, "add", "-A")[0] == 0
    assert (
        git(
            root,
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-qm",
            "work",
        )[0]
        == 0
    )
    # An explicit --branch must be a branch this checkout has, so a caller that
    # asks for one gets it; peek-ci names a branch but does not switch to it.
    if branch is not None:
        assert git(root, "branch", branch)[0] == 0
    return Workspace(root, phases, transport, calls, defaults)


def _row(name: str, bucket: str) -> dict[str, object]:
    return {
        "bucket": bucket,
        "state": bucket.upper(),
        "name": name,
        "link": f"https://example.test/{name}",
    }


def _open_pr(number: int = 11) -> list[dict[str, object]]:
    return [
        {
            "number": number,
            "state": "OPEN",
            "isDraft": True,
            "url": f"https://github.com/example/argus/pull/{number}",
        }
    ]
