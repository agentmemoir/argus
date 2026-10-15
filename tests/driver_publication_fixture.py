# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Real saved phase publication, with only GitHub and tmux transported locally."""

import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.reservations import ReservationStore, save_reservation_file
from argus.gitsync.git import current_branch, git
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.selftest_publish import _publish_fixture
from config_fixture import BASE

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver/phase-driver.sh"
PHASE = 100441


@dataclass
class DriverPublication:
    root: Path
    private: Path
    run: Path
    state: Path
    config: Path
    token: dict[str, Any]
    branch: str
    transport: Path
    calls: Path

    def save_transport(self, **overrides: Any) -> None:
        data = json.loads(self.transport.read_text())
        self.transport.write_text(json.dumps({**data, **overrides}))

    def tick(
        self, *modes: str, driver: Path = DRIVER
    ) -> subprocess.CompletedProcess[str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DRIVER_", "CLIO_", "ARGUS_"))
        }
        return subprocess.run(
            ["bash", str(driver), "--config", str(self.config), *modes],
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "receipt": json.loads((self.run / "publication.json").read_text()),
            "board": ReservationStore(self.root).read()[0],
            "branch": current_branch(self.root),
            "branches": git(self.root, "branch", "--format=%(refname:short)")[1],
            "claim": (self.state / "claim").read_text(),
            "halt": (self.state / "halted").read_text(),
            "tmux": [
                row
                for row in self.commands()
                if row[0] == "tmux" and row[1] in ("new-window", "new-session")
            ],
        }

    def commands(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(row) for row in self.calls.read_text().splitlines()]

    def prepare_group(self) -> None:
        """Publish both real group members while their external checks are pending."""
        store = ReservationStore(self.root)
        store.release_group(self.token)
        tokens = store.reserve_group([PHASE, PHASE + 1], "server-01")
        assert git(self.private, "fetch", "origin")[0] == 0
        assert git(self.private, "merge", "--ff-only", "origin/main")[0] == 0
        for token in tokens:
            number = int(token["phase"])
            document = self.private / "roadmap" / f"phase-{number}-member-{number}.md"
            for old in document.parent.glob(f"phase-{number}-*.md"):
                old.unlink()
            document.write_text(
                f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
                f"### Reservation Group\n\n`together: {PHASE}, {PHASE + 1}`\n"
            )
            run = self.run.parent / f"phase-{number}"
            run.mkdir(exist_ok=True)
            save_reservation_file(run / "reservation.json", token)
            (run / "run.json").write_text('{"state":"completed"}\n')
        assert git(self.private, "add", "-A")[0] == 0
        assert git(self.private, "commit", "-qm", "completed group")[0] == 0
        assert git(self.private, "push", "origin", "main")[0] == 0
        for token in tokens:
            published = publish_phase(
                self.root,
                token["phase"],
                pull_request="https://github.com/example/argus/pull/7",
            )
            assert published["state"] == "AWAITING_REVIEW", published
        self.token = tokens[0]
        (self.state / "claim").write_text(
            f"{PHASE}\t{self.token['reservation_id']}\t{self.token['generation']}\n"
        )


@pytest.fixture(name="driver_publication")
def driver_publication_fixture(
    tmp_path: Path, exec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> DriverPublication:
    monkeypatch.setattr(conventions, "_settings", {})
    conventions.configure({"commands.git": "git"})
    root, private, run = _publish_fixture(tmp_path, "saved-publication", PHASE)
    state = private / "runs/.driver"
    state.mkdir()
    exclude = private / ".git/info/exclude"
    exclude.write_text(exclude.read_text().rstrip("\n") + "\nruns/.driver/\n")
    assert git(private, "check-ignore", "runs/.driver/halted")[0] == 0
    plans = private / "roadmap"
    plans.mkdir()
    document = plans / f"phase-{PHASE}-settle-receipt.md"
    document.write_text(f"# {PHASE}. Settle receipt\n\n- [ ] Work to do.\n")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"run_dir: {private}/runs/phase-{{phase_number:06d}}\n"
        "inputs:\n  phase_number: {type: int}\n  phase_file: {}\n"
    )
    transport = tmp_path / "transport.json"
    calls = tmp_path / "transport.jsonl"
    transport.write_text(json.dumps({"state": "OPEN", "bucket": "pending"}))
    for name in ("gh", "tmux"):
        binary = exec_dir / name
        binary.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\nfrom pathlib import Path\n"
            f"state = json.loads(Path({str(transport)!r}).read_text())\n"
            "args = sys.argv[1:]\n"
            f"with Path({str(calls)!r}).open('a') as log:\n"
            f"    log.write(json.dumps([{name!r}, *args]) + '\\n')\n"
            + (
                "if args[:2] == ['pr', 'view']:\n"
                "    print(json.dumps({'state': state['state'], 'mergeCommit': "
                "{'oid': state.get('head')}, 'body': '', 'comments': []}))\n"
                "elif args[:2] == ['pr', 'checks']:\n"
                "    print(json.dumps([{'name': 'build', 'bucket': state['bucket']}]))\n"
                "elif args[:2] == ['pr', 'list'] or args[0] == 'api':\n"
                "    print('[]')\n"
                if name == "gh"
                else "if args[0] == 'list-windows':\n"
                "    print('@1 idle' if '#{window_id}' in args[-1] else 'idle')\n"
            )
        )
        binary.chmod(0o755)
    config = copy.deepcopy(BASE)
    config.update(
        {
            "repo-root": str(root),
            "pipeline": str(pipeline),
            "roadmap-dir": str(plans),
            "board": {
                "repo": str(private),
                "file": "claims.json",
                "branch": "main",
                "remote": "origin",
            },
            "notify": {
                "profile": "fixture",
                "channel": "discord:000000000000000000",
                "disabled": True,
            },
        }
    )
    config["driver"].update({"runs": str(private / "runs"), "state": ".driver"})
    config["commands"].update(
        {
            "python": sys.executable,
            "gh": str(exec_dir / "gh"),
            "tmux": str(exec_dir / "tmux"),
            "glide": sys.executable,
        }
    )
    (tmp_path / "machine.id").write_text("server-01\n")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    conventions.configure_from_cli(["--config", str(path)])
    branched = mode_branch(root, PHASE, document.name)
    assert branched["ok"], branched
    (root / "product.txt").write_text("completed phase product\n")
    for checkout in (root, private):
        assert git(checkout, "add", "-A")[0] == 0
        assert git(checkout, "commit", "-qm", "completed phase")[0] == 0
    published = publish_phase(
        root, PHASE, pull_request="https://github.com/example/argus/pull/7"
    )
    assert published["state"] == "AWAITING_REVIEW", published
    token = json.loads((run / "reservation.json").read_text())
    (state / "claim").write_text(
        f"{PHASE}\t{token['reservation_id']}\t{token['generation']}\n"
    )
    (state / "halted").write_text("manual hold\n")
    return DriverPublication(
        root, private, run, state, path, token, published["branch"], transport, calls
    )
