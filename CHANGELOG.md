# Changelog

Notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project intends to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html); no version has
been released yet.

## [Unreleased]

- `argus` ticks an unattended work queue: select a phase, reserve it on a shared
  Git board, launch its pipeline in tmux, watch the run, and check publication
  before the next tick.
- One JSONC configuration file names every path and command; a missing required
  key stops the tick and names the key.
- Reservation groups: several phases claimed in one board write, run on one
  branch, and published under one pull request, with the claim renewed while a
  member runs.
- Halt markers on blocked or failed runs, claim cooldown and expiry handling,
  and a remedy escalation that records open findings and releases the claim.
- Modes: `--check`, `--dry-run`, `--self-test`, `--stop`, and
  `--session <NUMBER> <PHASE-FILE>`.
- Checks: the driver self-test, the guard-faults harness, and a wheel that
  ships the shell driver.
- A publication lock left behind by a publish that died stops blocking after the
  30 minute heartbeat window, so claim recovery and other phases' publishes
  proceed. A fresh lock still blocks both; `release-publication` still frees one
  early.
- A relative `--repo-root` or `--roadmap-dir` resolves against the working
  directory, not the configuration file's folder, so `--root .` from the project
  root names the project root.
- The tmux idle watcher ends when the process that started it is gone, so one
  run outside tmux, or after its tmux server died, no longer loops as an orphan.
- A reservation group's pull request is made ready for review only when every member has published and its checks pass; a pull request with failing, running, or unreported checks stays in draft with the reason in the publication output, and the driver readies it on the first tick that finds the checks green.
- Publishing a group member merges the open pull requests that target the group branch when they are ready, from this repository, mergeable, not blocked by a review, and green, so a fix for a bug an earlier member left lands before the next member starts. The new `--mode gate --phase NUMBER` runs the same step by hand.
- Publishing the same head twice posts the member's completion comment once.
- The publication output no longer warns that a group member's branch was cut from the earlier member of the same group.
- A tick that finds the queue empty now runs the merge check on the pull requests still waiting for review. It used to refuse with "coordination state unavailable" on every tick, because it read the selector's exit code, where 1 means nothing is left to reserve, and the last pull requests of a queue were never recorded as merged. The driver now judges whether the selector printed a state, so a selector that gave none still refuses, and a queue waiting on a decision (`BLOCKED_ON_GATE`) is reported as waiting. The selection asked again after the merge check is judged the same way, so the tick that records the last merged pull request of a queue no longer ends in a refusal.
- A line that repeats from tick to tick (the configuration, a renewed claim, a waiting pull request, the selector's warnings, an empty queue, a halt) is logged when it changes, not on every tick. The log is still touched on each tick.
