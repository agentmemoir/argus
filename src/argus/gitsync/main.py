# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed git sync for the public and private checkouts.

The command line: arguments, the mode dispatch, and the JSON result.

Before a phase starts, and again before finalize publishes, the pipeline
must not run on a stale or diverged base. This module fetches each checkout
from its own configured upstream and brings it in line by fast-forward or a
plain merge, stopping the line only when it cannot do so safely.

The configured run directory must be inside the private checkout for
publication, because phase receipts and run state are committed there.

Two modes, because "local is ahead" means different things:

* ``start`` (before a phase): bring the checkout in line with its remote.
  A strictly-behind branch is fast-forwarded; a diverged branch is merged
  with a plain merge commit that keeps every local commit and accepts the
  remote changes. Local commits ahead of the remote are fine here: the
  remote has nothing new, so nothing is pulled and those commits publish at
  finalize.
* ``push`` (before finalize pushes): the phase's own commits are local, so
  an ahead branch is verified as a fast-forward push. A diverged or behind
  remote is merged (or fast-forwarded) first so the push can proceed; a
  conflict is left in place (not aborted) for the finalize agent to resolve.

``commit_leftover_run_state`` is the fresh-start cleanup: when given a phase,
it commits and pushes only that phase's private ``runs/phase-<N>`` leftovers
(the runner writes ``ledger.json``/``run.json`` after finalize's commit) and
refuses staged paths outside that phase. ``--mode publish`` additionally
checks the phase's three-part reservation fence, takes the short publication
lock, verifies effective public/private Git push destinations, writes and pushes
``runs/phase-<N>/publication.json``, and performs the final sync-and-push
without force.

Hard safety rules, enforced by construction:

* Never ``git pull`` (this repo sets ``pull.rebase = true``, which would
  rewrite history), never rebase, never force-push, never reset, never
  stash, never ``clean``. Fast-forward when possible, otherwise a plain
  merge commit; a real conflict is aborted in start mode.
* Unreviewed work is never at risk from a sync attempt. Every precondition is
  tested *before* git is asked to change anything, so a refusal is always
  reached with the index and the working tree exactly as they were.
* A dirty index is not a reason to stop on its own. A phase stages its work
  and never commits it, so the index is dirty for the whole life of a phase
  and a blanket refusal strands any phase whose remote moved. What stops an
  integration is an *overlap* between the paths the incoming commits change
  and the paths local work has touched. How much overlap is fatal depends on
  the relation, because git's own guard does:

  - strictly behind (a fast-forward): git carries a dirty index, staged or
    not, provided the incoming commits do not touch the same paths. So the
    overlap test is the whole gate. Measured on git 2.53.0.
  - diverged (a real merge): git refuses to merge at all while the index
    differs from HEAD, whether or not the paths are disjoint, and the refusal
    is the same under every merge strategy. Measured on git 2.53.0. So the
    index stays a hard gate here, and only unstaged and untracked work is
    allowed through, and only when it does not overlap.

  Both were re-measured on the toolchain this pipeline runs before either
  rule was written. Treat them as measurements, not as guarantees, and
  re-measure if git changes: the fast-forward case is the one that would
  silently start failing, and it fails safe (git refuses, we report it).
* Giving up is the last resort: a mechanical fast-forward or a clean merge
  is preferred. Only a genuine conflict, an overlapping local edit, or a
  detached HEAD stops the line, and then local work is reported, never
  discarded.
* The configured private checkout is synced only from its own
  remote; effective public/private fetch and push destinations are compared,
  so private content cannot cross into the public checkout.

Usage (name the source tool and the project root):

    python3 /path/to/argus/gitsync/main.py --root /path/to/project --mode start
    python3 /path/to/argus/gitsync/main.py --root /path/to/project --mode push
    python3 /path/to/argus/gitsync/main.py --root /path/to/project --mode publish --phase 100440
    python3 /path/to/argus/gitsync/main.py --self-test

Exit codes: 0 synced/verified, 1 blocked (operator decision required),
2 config or usage error. JSON goes to stdout in every case.
"""

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.config.loader import ConfigError as ArgusConfigError
from argus.gitsync.git import ConfigError, summarize
from argus.gitsync.issue_modes import issue_mode
from argus.gitsync.merge import sync_one
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.mode_clean import mode_checkpoint, mode_clean
from argus.gitsync.modes import mode_published, mode_tidy, result_exit_code
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.selftest import self_test
from argus.gitsync.sync import mode_resync, sync_all


def main() -> int:
    ap = argparse.ArgumentParser(description="Fail-closed git sync.")
    ap.add_argument(
        "--root",
        "--repo-root",
        dest="config_repo_root",
        help="repo root; syncs it and the configured private checkout",
    )
    ap.add_argument("--config")
    ap.add_argument("--kind", choices=["phase", "issue"], default="phase")
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
        issue_result = issue_mode(args) if args.kind == "issue" else None
        if issue_result is not None:
            out = issue_result
        elif args.mode == "branch":
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


if __name__ == "__main__":
    sys.exit(main())
