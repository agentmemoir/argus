# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The command line: arguments, the mode dispatch, and the JSON result."""

import argparse
import json
from pathlib import Path

from argus import conventions
from argus.config import ConfigError as ArgusConfigError
from argus.gitsync_git import ConfigError, summarize
from argus.gitsync_merge import sync_one
from argus.gitsync_mode_branch import mode_branch
from argus.gitsync_mode_clean import mode_checkpoint, mode_clean
from argus.gitsync_modes import mode_published, mode_tidy, result_exit_code
from argus.gitsync_publish_phase import publish_phase
from argus.gitsync_selftest import self_test
from argus.gitsync_sync import mode_resync, sync_all


def main():
    ap = argparse.ArgumentParser(description="Fail-closed git sync.")
    ap.add_argument(
        "--root",
        "--repo-root",
        dest="config_repo_root",
        help="repo root; syncs it and the configured private checkout",
    )
    ap.add_argument("--config")
    ap.add_argument("--driver", dest="config_driver")
    ap.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    ap.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    ap.add_argument("--repo", default=None, help="sync one checkout instead of --root")
    ap.add_argument(
        "--mode",
        choices=[
            "start",
            "push",
            "publish",
            "branch",
            "published",
            "resync",
            "tidy",
            "clean",
            "checkpoint",
        ],
        default="start",
        help="start: sync before a phase; push: verify a "
        "fast-forward; publish: claim-check and push a phase's "
        "branch; branch: put the public checkout on the phase "
        "branch; published: confirm a human merged the pull "
        "request, mark the phase published, and return the "
        "public checkout to the integration branch; resync: "
        "merge the integration branch into the phase branch, "
        "leaving a conflict for the agent to finish; tidy: "
        "return a checkout to the integration branch when the "
        "phase it is on is already recorded as published; "
        "clean: commit and push leftover private run state, "
        "for one --phase or for every phase when it is omitted; "
        "checkpoint: commit and push a checkout's work in "
        "progress on the branch it is on, for a release handoff",
    )
    ap.add_argument(
        "--phase", default=None, help="phase number for --mode publish or --mode clean"
    )
    ap.add_argument(
        "--reservation-file",
        default=None,
        help="phase-local reservation.json for --mode publish",
    )
    ap.add_argument("--machine-id", default=None)
    ap.add_argument("--reservation-id", default=None)
    ap.add_argument("--generation", type=int, default=None)
    ap.add_argument(
        "--self-test", action="store_true", help="run stubbed-remote checks and exit"
    )
    ap.add_argument(
        "--roadmap", default=None, help="phase roadmap file name, for --mode branch"
    )
    ap.add_argument(
        "--group",
        action="append",
        default=None,
        help="a member of the reservation group this branch covers, "
        "for --mode branch; repeat once per member",
    )
    ap.add_argument(
        "--branch",
        default=None,
        help="the branch the public checkout must be on, for the start and push modes",
    )
    ap.add_argument(
        "--pull-request",
        default=None,
        help="pull request URL finalize opened, recorded in the "
        "receipt for --mode publish",
    )
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        conventions.configure_from_args(args)
        args.root = str(conventions.setting("repo-root"))
        if args.mode == "branch":
            if args.phase is None or not args.roadmap:
                raise ConfigError(
                    "--phase and --roadmap are required with --mode branch"
                )
            out = mode_branch(
                args.root,
                args.phase,
                args.roadmap,
                conventions.setting("roadmap-dir"),
                group=args.group,
            )
        elif args.mode == "publish":
            if args.phase is None:
                raise ConfigError("--phase is required with --mode publish")
            out = publish_phase(
                Path(args.root).resolve(),
                args.phase,
                reservation_file=args.reservation_file,
                machine_id=args.machine_id,
                reservation_id=args.reservation_id,
                generation=args.generation,
                pull_request=args.pull_request,
            )
        elif args.mode == "published":
            if args.phase is None:
                raise ConfigError("--phase is required with --mode published")
            out = mode_published(args.root, args.phase)
        elif args.mode == "clean":
            out = mode_clean(args.root, args.phase)
        elif args.mode == "resync":
            out = mode_resync(args.root)
        elif args.mode == "tidy":
            out = mode_tidy(args.root)
        elif args.mode == "checkpoint":
            out = mode_checkpoint(args.root)
        elif args.repo:
            res = sync_one(Path(args.repo).resolve(), args.mode, branch=args.branch)
            out = {
                "ok": res["ok"],
                "mode": args.mode,
                "repos": [res],
                "summary": summarize([res], args.mode),
            }
        else:
            out = sync_all(Path(args.root).resolve(), args.mode, branch=args.branch)
    except (ArgusConfigError, ConfigError, ValueError) as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": args.mode,
                    "state": "config_error",
                    "error": str(e),
                },
                indent=2,
            )
        )
        return 2
    print(json.dumps(out, indent=2))
    return result_exit_code(out)
