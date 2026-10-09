# Paths and Ownership

The pipeline names each phase run directory. The run directory holds `run.json`, `ledger.json`, the reservation token, and `publication.json`. There is no Argus run-directory flag; the pipeline's `run_dir` key owns the value.

Publication commits run state in the configured board checkout, so a publishing pipeline puts its run directory there. A run directory outside that checkout is refused before staging. The configured run folder holds the driver log, lock, and halt marker.

The board branch is never checked out. Reading the board does not create it; an explicit migration or the first write creates it from the remote integration branch. A board is shared: every machine that reserves work on it names the same `board.file` and `board.branch`.

## Reservation Groups

A phase document may declare a reservation group: several phases one owner runs as one unit. The whole group is claimed in one board write under one reservation id, its members run one per tick on one branch, and one pull request covers them all, while each member keeps its own receipt under its own run directory.

The tick renews the group while a member runs, `--release` gives the whole group back, and a member that is already published is never undone by a release or an expiry.

Every member records the group's single pull request in its receipt, and a member reuses a sibling's recorded pull request only while GitHub still reports it open: once that pull request merges, the branch is reset and a new pull request covers the members still to run. A publication result names the group, the members still to publish (`group_pending`), and whether this member was the last to publish (`group_last_unfinished`). When the last member publishes, the helper marks the group's shared pull request ready for review, so a group can hold one draft pull request until its work is complete.

## Issue Work

An issue machine keeps its claims in `board.issues-file`, a second file on the same board branch. A phase claim and an issue claim never share a record, even for the same number. The pipeline's `run_dir` names each issue run directory, for example `runs/issue-000295`, and the issue's reservation token lives there. The frozen task file lives in `issues.task-dir`, which must be inside the checkout. An issue run writes no `publication.json`: GitHub's own issue and pull request state is the completion record.

## The Glide Contract

The configured `glide` command runs the pipeline as `glide run --pipeline <file> --input <name>=<value>`. A missing command stops the tick before a claim. Argus names no Glide source tree, sets no `PYTHONPATH`, and keeps no fallback copy. The pipeline's stages and hooks are project content, so Argus needs no project `scripts/` folder.

Argus does not search for project folders. The config file names every project path, so a missing path stops the tick with that path in the error.

## Work Items

Work-item files end in `.md`. The first number separated by `-` or `_` is the phase number, and the text after it is the branch slug. Appendix files are skipped. For example, `work-100010-cache.md` matches an include glob of `work-*.md`.
