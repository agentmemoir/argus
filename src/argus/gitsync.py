#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed git sync for the public and private checkouts.

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

    python3 /path/to/argus/gitsync.py --root /path/to/project --mode start
    python3 /path/to/argus/gitsync.py --root /path/to/project --mode push
    python3 /path/to/argus/gitsync.py --root /path/to/project --mode publish --phase 100440
    python3 /path/to/argus/gitsync.py --self-test

Exit codes: 0 synced/verified, 1 blocked (operator decision required),
2 config or usage error. JSON goes to stdout in every case.
"""

import sys

# Source commands import the package from their own folder.
if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus.gitsync_main import main
from argus.gitsync_review import awaiting_review as awaiting_review

if __name__ == "__main__":
    sys.exit(main())
