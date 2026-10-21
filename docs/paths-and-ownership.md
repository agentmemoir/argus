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

## Write Paths And Their Fence

Every path that writes reads the board fence before its first write, and a fence that does not hold writes nothing. The fence is the phase's own `reservation.json` (`machine_id`, `reservation_id`, `generation`) proved with `ReservationStore.assert_owner` through one helper, `argus.gitsync.fence.phase_fence`; the answer is `FENCED` when the board says this machine, reservation, or generation is not the owner, and `COORDINATION_UNAVAILABLE` when the reservation cannot be read or the board cannot answer. Both are refusals: the command exits non-zero and the file, the ref, and the board are exactly as they were.

| Path | First write | Reads the board today | Exemption or refusal rule |
|---|---|---|---|
| `gitsync --mode published` | the phase receipt | yes, before the write | Fenced. `FENCED` or `COORDINATION_UNAVAILABLE`, exit 2 or 1, no receipt |
| `--release` | the checkpoint push | yes, before the interrupt and the push | Fenced. A missing, incomplete, or mismatched claim refuses; no checkpoint, no release |
| `abandon` | `abandoned.json` residue | yes, before the residue | Fenced. A foreign fence leaves the claim intact and writes no residue. The board group is released **before** the residue is written, so a release that fails parks nothing and leaves the claim as it was: the one state either way is released and parked, or owned and unparked |
| `record_claim` | the claim file | yes, before the claim | Fenced. A pair the board does not confirm writes no claim |
| `gitsync --mode ready` | nothing | no board read; it reads GitHub's ready/draft timeline | Exempt: it is a sweep that writes no file, no ref, and no receipt, holds no claim, and changes no verdict. The transition it takes is guarded by GitHub's own timeline |
| `--stop` | `$HALT`, then `stopped-claim` | yes, before both files | Fenced. A bad, empty, unreadable, or mismatched fence writes neither file and exits 1 |
| `gitsync --mode tidy --phase N` | the checkout move | yes, before either checkout moves | Fenced when a phase is named |
| `gitsync --mode tidy` (pre-sync) | the checkout move | no | Exempt: it runs before a phase is selected, so there is no phase to name. It still refuses a dirty tree, so it never moves a checkout that would lose work |
| `gitsync --mode clean --phase N` | the run-state commit and push | yes, before the commit | Fenced. `FENCED` writes no commit and pushes nothing |
| `gitsync --mode clean` (whole sweep) | each planned phase's run-state commit | yes, per planned phase | Fenced per phase. A terminal receipt is exempt; a phase whose fence does not hold is skipped and named, and the sweep continues |
| `gitsync --mode checkpoint --phase N` | the commit and push | yes, before the stage | Fenced when a phase is named; `release`, its only production caller, always names one |
| `gitsync --mode branch` | the branch cut | yes, before either checkout is touched | Fenced. A refusal creates no branch and leaves both refs where they were |

`--mode published`, `--release`, `abandon`, and `record_claim` were already fenced; `--stop`, `--mode tidy` with a phase, `--mode clean` (one phase and the whole sweep), `--mode checkpoint` with a phase, and `--mode branch` are fenced as of the same change. `gitsync --mode ready` is the one path that writes nothing, so there is nothing for a fence to protect.

Every row above has a test that drives the real writer and asserts the refusal wrote nothing: `tests/test_driver_write_fences.py` for `stop`, `tidy` (with a phase and the pre-sync exemption), `clean` (one phase and the sweep), `checkpoint`, and `branch`, one negative and one positive case each; `tests/test_driver_abandon_rollback.py` for the `abandon` ordering, with a failing and a succeeding board release; `tests/test_driver_release_lock.py` for `--release`'s lock, `tests/test_driver_claim_fencing.py` for `record_claim`, `tests/test_publish_separation.py` for `--mode published`, and `tests/test_group_ready_tick.py` for the `--mode ready` sweep on the real run directory.

`abandon` releases the board group before it writes the residue, and stops there when the release fails. That order is what makes the outcome one of two states rather than three: released and parked, or still owned and unparked. Writing the residue first would leave a parked phase whose claim this machine still holds, which no owner will run and no other machine may take; an unpark written after a failed release would be a second write that can itself fail, so there is none.

The whole-tree `--mode clean` sweep has no single owner, so it proves the fence per planned phase rather than refusing the sweep. A phase whose receipt reads `published` is exempt by construction: its run is recorded as finished, nothing the sweep lands can change that verdict, and a released claim is the ordinary end state. Every other phase must prove the fence; one that cannot is skipped with its phase number and reason in the result's `skipped` array and the summary, and the other phases still land. A hard fail would strand post-merge run state that the next phase's publish then refuses on.

The pre-selection `--mode tidy` call is the one write path with no phase, because it runs before a phase is selected. It stays unfenced and keeps its own guard: it refuses a dirty tree, a phase that is not published, and a missing integration branch, so it cannot move a checkout that would lose work. The call from the branch cut knows the phase and passes it, and that call is fenced.

A stop with no provable fence writes neither the halt marker nor the manual-stop hold. That is deliberate: the marker is what holds the line, and a machine whose claim expired or was taken over must not write it. `kill-session` and a hand-written halt marker remain the operator's last resort when no fence holds.

## Reservation Groups

A phase document may declare a reservation group: several phases one owner runs as one unit. The whole group is claimed in one board write under one reservation id, its members run one per tick on one branch, and one pull request covers them all, while each member keeps its own receipt under its own run directory.

The tick renews the group while a member runs, `--release` gives the whole group back, and a member that is already published is never undone by a release or an expiry.

Every member records the group's single pull request in its receipt, and a member reuses a sibling's recorded pull request only while GitHub still reports it open: once that pull request merges, the branch is reset and a new pull request covers the members still to run. A publication result names the group, the members still to publish (`group_pending`), and whether this member was the last to publish (`group_last_unfinished`). The group's shared pull request stays in draft until every member has published and its checks pass, as the next section describes.

## The Group Gate

A group's shared pull request stays in draft until the group is done. When a member publishes, the publish step checks whether any member is still to publish; once every member has published, the last one asks GitHub whether the shared pull request's checks all pass, and only then marks it ready for review.

The transition is gated on the checks: gh must report at least one check, and every reported check must read pass. A pull request whose checks are failing, still running, or not reported at all stays in draft, and the result says which.

The check read names the pull request's own `[HOST/]OWNER/REPO` on the `gh pr checks` call, so a run directory outside a checkout, or one on a different repository, cannot read another repository's checks and report them as this group's.

The ready is retried on every driver tick by `gitsync --mode ready`, which sweeps every phase receipt rather than the phases waiting on a merge, so it keeps retrying until the pull request is readied or the timeline shows a prior ready, whatever state the phase's own receipt is in. A receipt that already reads `published` is the case that matters: it leaves the pending list, so a retry placed on the `--mode published` path would run once and never again. The sweep writes no phase state, holds no claim, and never changes the published or merge verdict.

The transition happens once: GitHub's own ready/draft timeline is the once-guard. A pull request whose timeline holds any ready event is skipped, so a person who sets it back to draft is not undone on a later tick, and no local record is consulted. The ready-once record an earlier revision wrote beside the receipt is gone: no production writer ever emitted a version a reader accepted, the version-2 attempt it did write was discarded, and the timeline already answers the question, so the sweep carries no local state at all.

A failed transition is reported, never raised, because the phase's commits are already on the branch and its receipt is written. The failure is named in the sweep's result and the next tick tries again; a person is not put on the critical path to notice it.

## Issue Work

An issue machine keeps its claims in `board.issues-file`, a second file on the same board branch. A phase claim and an issue claim never share a record, even for the same number.

The pipeline's `run_dir` names each issue run directory, for example `runs/issue-000295`, and the issue's reservation token lives there. The frozen task file lives in `issues.task-dir`, which must be inside the checkout. An issue run writes no `publication.json`: GitHub's own issue state is the completion record.

## The Glide Contract

The configured `glide` command runs the pipeline as `glide run --pipeline <file> --input <name>=<value>`. A missing command stops the tick before a claim. Argus names no Glide source tree, sets no `PYTHONPATH`, and keeps no fallback copy. The pipeline's stages and hooks are project content, so Argus needs no project `scripts/` folder.

Argus does not search for project folders. The config file names every project path, so a missing path stops the tick with that path in the error.

## Work Items

Work-item files end in `.md`. The first number separated by `-` or `_` is the phase number, and the text after it is the branch slug. Appendix files are skipped. For example, `work-100010-cache.md` matches an include glob of `work-*.md`.

See [configuration.md](configuration.md) for every configuration key, and [running.md](running.md) for the run modes.
