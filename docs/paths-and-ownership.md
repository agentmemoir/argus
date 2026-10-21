# Paths and Ownership

The pipeline names each phase run directory. The run directory holds `run.json`, `ledger.json`, the reservation token, and `publication.json`. There is no Argus run-directory flag; the pipeline's `run_dir` key owns the value.

Publication commits run state in the checkout that holds the run directory, which the pipeline's run_dir names; the board checkout may be a different repository. A `runstate.repo` setting names that checkout explicitly. The configured run folder holds the driver log, lock, and halt marker.

The board branch is never checked out. Reading the board does not create it; an explicit migration or the first write creates it from the backup ref, or from the remote integration branch when no backup exists. A board is shared: every machine that reserves work on it names the same `board.file` and `board.branch`.

## Board Branch Backup And Recovery

The board branch carries a second ref, the board branch's name plus `--argus-backup` (`claims` has `claims--argus-backup`), or the name in `board.backup-branch`. Each successful board write advances the backup ref toward the board branch's new commit with a non-forced push. Because the push is not forced, a ref that has moved on is rejected and reported, not overwritten. A backup-push failure, including a timeout, is reported on stderr and the board write still succeeds; the backup catches up on the next write. The backup is an ordinary branch, so a restore needs no machine-local clone and no branch protection.

A missing board branch is reseeded from the backup ref first, and only falls back to the remote integration branch when no backup exists. A deleted board branch therefore comes back with the state it last held on the next migration or write; the state file that left the integration tip is read from the backup, not the tip. Deleting the board branch no longer resets the board: to reset it, delete the backup ref too.

Restore by hand, from any clone, with the board branch named `BOARD`:

```sh
git fetch origin refs/heads/BOARD--argus-backup:refs/heads/BOARD--argus-backup
git push origin refs/heads/BOARD--argus-backup:refs/heads/BOARD
```

Verify the restored board reads as initialized: run the board reader (`python -m argus.board.reservations show`) and confirm it prints the coordination state. A board whose state file was lost instead fails with `coordination state file was deleted from an initialized board`.

## Reservation Groups

A phase document may declare a reservation group: several phases one owner runs as one unit. The whole group is claimed in one board write under one reservation id, its members run one per tick on one branch, and one pull request covers them all, while each member keeps its own receipt under its own run directory.

The tick renews the group while a member runs, `--release` gives the whole group back, and a member that is already published is never undone by a release or an expiry.

Every member records the group's single pull request in its receipt, and a member reuses a sibling's recorded pull request only while GitHub still reports it open: once that pull request merges, the branch is reset and a new pull request covers the members still to run. A publication result names the group, the members still to publish (`group_pending`), and whether this member was the last to publish (`group_last_unfinished`). The group's shared pull request stays in draft until every member has published and its checks pass, as the next section describes.

## The Group Gate

A group's shared pull request stays in draft until the group is done. When a member publishes, the publish step checks whether any member is still to publish; once every member has published, the last one asks GitHub whether the shared pull request's checks all pass, and only then marks it ready for review.

The transition is gated on the checks: gh must report at least one check, and every reported check must read pass. A pull request whose checks are failing, still running, or not reported at all stays in draft, and the result says which.

The transition happens once: a ready-once record is written beside the last member's receipt, so a person who sets the pull request back to draft is not undone on a later run.

A failed transition is reported, never raised, because the phase's commits are already on the branch and its receipt is written. When the ready lands but the record write fails, the result says both: the pull request is non-draft, and the record is missing, so a rerun re-readies it.

## Issue Work

An issue machine keeps its claims in `board.issues-file`, a second file on the same board branch. A phase claim and an issue claim never share a record, even for the same number.

The pipeline's `run_dir` names each issue run directory, for example `runs/issue-000295`, and the issue's reservation token lives there. The frozen task file lives in `issues.task-dir`, which must be inside the checkout. An issue run writes no `publication.json`: GitHub's own issue state is the completion record.

## The Glide Contract

The configured `glide` command runs the pipeline as `glide run --pipeline <file> --input <name>=<value>`. A missing command stops the tick before a claim. Argus names no Glide source tree, sets no `PYTHONPATH`, and keeps no fallback copy. The pipeline's stages and hooks are project content, so Argus needs no project `scripts/` folder.

Argus does not search for project folders. The config file names every project path, so a missing path stops the tick with that path in the error.

## Work Items

Work-item files end in `.md`. The first number separated by `-` or `_` is the phase number, and the text after it is the branch slug. Appendix files are skipped. For example, `work-100010-cache.md` matches an include glob of `work-*.md`.

See [configuration.md](configuration.md) for every configuration key, and [running.md](running.md) for the run modes.
