# Paths and Ownership

The pipeline names each phase run directory. The run directory holds `run.json`, `ledger.json`, the reservation token, and `publication.json`. There is no Argus run-directory flag; the pipeline's `run_dir` key owns the value.

Publication commits run state in the checkout that holds the run directory, which the pipeline's run_dir names; the board checkout may be a different repository. A `runstate.repo` setting names that checkout explicitly. The configured run folder holds the driver log, lock, and halt marker.

The board branch is never checked out. Reading the board does not create it; an explicit migration or the first write creates it from the remote integration branch. A board is shared: every machine that reserves work on it names the same `board.file` and `board.branch`.

## Board Branch Backup And Recovery

The board branch carries a second ref, its name with `-backup` appended: `claims` has `claims-backup`. Every board write advances the backup to the same commit as the board branch in one atomic push, so the backup is never behind the board it mirrors. The backup is an ordinary branch, so a restore needs no machine-local clone and no branch protection.

A missing board branch is reseeded from the backup ref first, and only falls back to the remote integration branch when no backup exists. A deleted board branch therefore comes back with the state it last held on the next migration or write; the state file that left the integration tip is read from the backup, not the tip.

Restore by hand, from any clone, with the board branch named `BOARD`:

```sh
git fetch origin refs/heads/BOARD-backup:refs/heads/BOARD-backup
git push origin refs/heads/BOARD-backup:refs/heads/BOARD
```

Verify the restored board reads as initialized: run the board reader (`python -m argus.board.reservations show`) and confirm it prints the coordination state. A board whose state file was lost instead fails with `coordination state file was deleted from an initialized board`.

## Reservation Groups

A phase document may declare a reservation group: several phases one owner runs as one unit. The whole group is claimed in one board write under one reservation id, its members run one per tick on one branch, and one pull request covers them all, while each member keeps its own receipt under its own run directory.

The tick renews the group while a member runs, `--release` gives the whole group back, and a member that is already published is never undone by a release or an expiry.

Every member records the group's single pull request in its receipt, and a member reuses a sibling's recorded pull request only while GitHub still reports it open: once that pull request merges, the branch is reset and a new pull request covers the members still to run. A publication result names the group, the members still to publish (`group_pending`), and whether this member was the last to publish (`group_last_unfinished`). The group's shared pull request stays in draft until every member has published and its checks pass, as the next section describes.

## The Group Gate

The group gate runs when a member publishes, and again on every tick that finds a published member waiting for its pull request to merge (`--mode gate --phase NUMBER` runs it by hand). It does two things.

First, it merges the open pull requests whose base is the group branch, so a fix for a bug an earlier member left reaches the branch before the next member starts. A pull request is merged only when it is ready for review, comes from this repository and not from a fork, is mergeable, has no review requesting changes, and has passing checks.

The merge is pinned to the head those checks were read for and never uses administrator rights. Any other pull request stays open, and the result names it with the reason.

Second, once every member has published, it makes the shared pull request ready for review, but only when the checks on it all pass. A pull request whose checks are failing, still running, or not reported yet stays in draft, and the result says which.

A fix merged by the same run holds it too, because the merge moves the head the checks judge. The transition happens once, so a person who sets the pull request back to draft is not undone on the next tick.

The result is the `group_gate` object in the publication output: `state` (`WAITING` while a member is still to publish, `HELD`, or `READY`), the `reasons` it is held, and `fix_pull_requests` with the numbers merged and the pull requests held. The driver logs a changed answer once, not on every tick.

## Repair

With a `repair` object in the configuration, a tick that is about to select work first looks at the pull requests this machine published that are still open. One whose checks have all finished, with at least one failed, gets one repair run. The driver puts the checkout on the pull request's branch, writes a task file (the failing checks, the lines of each failed workflow run that explain the failure, and, for a failing check with no workflow run, the pull request's open code-scanning alerts), and runs the configured pipeline once in the phase window. The whole failed log and the whole alert list are kept in the run's folder beside the task. The task file sits in the round's own folder, named `<pr>-<round>`, and its header names both numbers, the attempt the budget counts and the round: `Attempt: 2 of 3 (round 5)`.

The pipeline takes the inputs `repair_id` and `repair_task` and keeps its run folder under the driver's state folder. Its agent fixes and commits on the branch, the driver pushes without force, and the next tick reads the checks of the new head.

With `repair.verify` set, a round runs that command over the paths the repair changed since the commit its first round started from, after the push and before the next tick counts the round. The paths arrive in `ARGUS_REPAIR_CHANGED`, one per line, and that commit in `ARGUS_REPAIR_BASE`. Before the push the round writes `verify.id`, its own marker, beside `verify.paths` and `verify.command`, and after the command runs it writes `verify.log` and `verify.rc`. An answer whose `verify.id` is not this round's marker is another round's answer and is ignored, and a marker with no `verify.rc` yet reads as running. A push that fails or pushes nothing clears those five files, so that round is counted as it was before. `repair.verify-timeout` bounds the run through `timeout`, or through `gtimeout` where `timeout` is not on PATH; with neither on PATH the command runs unbounded and the driver logs one line saying so. No command, no changed path, or a base git cannot read runs no check at all.

A round whose check fails is a self-introduced regression. The driver logs it by that name, the next task carries the check's output and the paths it covered, and the round does not spend an attempt, so the budget stays open for the failure that round was given. Rounds are capped at twice `repair.attempts`, so a repair that keeps regressing still ends, and a round is counted as an attempt when the next turn reads its check as passing or finds none. A check that is still running when the checks settle holds the tick for `repair.wait`, as an unfinished read of the checks themselves does, and it is counted as an attempt when that wait runs out, so a check that never answers cannot hold a pull request open. With `repair.wait` at 0 nothing is held: a check still running is counted at once, and that attempt is spent.

This covers both moments a red result matters. After a group's last member publishes, the machine is idle, so the next tick repairs the group's pull request before it is readied. Between two members the repair check comes before the next member is reserved, so a failure does not ride along into that member's work.

Checks that are still running, or not reported yet, hold the tick for up to `repair.wait` seconds, because a result for a head that has been replaced is never seen again. A `wait` of 0 never holds.

Each pull request gets `repair.attempts` counted attempts, and no more than twice that many rounds when a round's own check fails, and the count starts again when its checks pass. When the count is used up, the driver logs it, sends one notice, and goes on with its normal work: a pull request that cannot be repaired does not stop the line. A repair run never writes the halt marker.

A run that ends without a repair and leaves uncommitted edits sends a notice, and the next repair does not switch branches over those edits. `--mode repair` prints the plan the tick acts on, and its records live in the state folder's `repair/` folder, which is untracked.

A repair session runs in the phase window, and every tick skips while that window is alive, so a session that never ends would stop the line with nothing said. The launch writes `repair-<id>.start` in the state folder and the session removes it when it ends, on the normal path and on a hangup or a signal. With `repair.stale-after` above 0, a tick that finds the window alive and that record at least that many seconds old logs one line naming the id, the age and the limit, and sends one notice per session, guarded by `repair-<id>.stale`. The watch reports only: it kills nothing, because a slow check and a hung one look the same from there.

## Issue Work

An issue machine keeps its claims in `board.issues-file`, a second file on the same board branch. A phase claim and an issue claim never share a record, even for the same number.

The pipeline's `run_dir` names each issue run directory, for example `runs/issue-000295`, and the issue's reservation token lives there. The frozen task file lives in `issues.task-dir`, which must be inside the checkout. An issue run writes no `publication.json`: GitHub's own issue and pull request state is the completion record.

## The Glide Contract

The configured `glide` command runs the pipeline as `glide run --pipeline <file> --input <name>=<value>`. A missing command stops the tick before a claim. Argus names no Glide source tree, sets no `PYTHONPATH`, and keeps no fallback copy. The pipeline's stages and hooks are project content, so Argus needs no project `scripts/` folder.

Argus does not search for project folders. The config file names every project path, so a missing path stops the tick with that path in the error.

## Work Items

Work-item files end in `.md`. The first number separated by `-` or `_` is the phase number, and the text after it is the branch slug. Appendix files are skipped. For example, `work-100010-cache.md` matches an include glob of `work-*.md`.

See [configuration.md](configuration.md) for every configuration key, and [running.md](running.md) for the run modes.
