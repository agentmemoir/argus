# Running Argus

One configuration file per tick, and one command. The file names every value the tick uses, including the commands it runs.

Argus needs Bash, Git, tmux, and a Python with PyYAML. The publisher uses the configured `gh`; notifications are optional.

Each driver needs its own checkout. Give every driver config its own `repo-root`. Argus refuses a second driver on a checkout that another driver's phase holds. See [Two drivers on one machine](#two-drivers-on-one-machine).

## Install

```sh
make install                     # link this working tree onto PATH
argus --config ~/etc/project-argus.jsonc --check
argus --config ~/etc/project-argus.jsonc --dry-run
```

`make install` links the source command into `~/.local/bin`. Add that folder to `PATH`.

Use `BINDIR` to choose another folder. Edits in the tree are live.

`pip install .` and `uv tool install .` install Argus as a package with a console script. The package ships the shell driver and the helpers beside it, because the command is the driver. `make wheel` builds it and checks that the driver is inside.

`bin/argus` is the development command, and the installed `argus` console script is the packaged one. Both call the same shell driver.

## Modes

Pass one mode flag:

- `--check` — the readiness report. It prints every configured value with the flag-or-file source it came from, checks the commands and folders the tick will use, runs the pipeline's `pre_run` hook once, runs Argus's own self-tests, and exits nonzero when it finds a problem. A refusing `pre_run` hook fails the check with the hook's first line and the action to take. The check runs the hook for real, so a `pre_run` hook must be read-only: Glide passes no flag or variable that marks a check call. The check runs the hook from the repository root with `GLIDE_HOOK_POINT`, `GLIDE_PIPELINE`, and `GLIDE_STATE` set as a run sets them; `GLIDE_RUN_DIR` and `GLIDE_RUN_ID` are not set, because no run exists yet. The host override file `glide/hooks.toml` in the XDG config folder replaces the pipeline's `pre_run`, as it does for Glide.
- `--dry-run` — what the tick would do, without launching anything.
- `--merge-check-only` — settle phase merges and group readiness without selecting or launching work.
- `--self-test` — the driver's own test. It needs no configuration file and starts no agent.
- `--stop` — halt the live run.
- `--release` — stop the live run and give its reservation group back.
- `--clear-halt` — remove the halt and stall markers, and the halt record when the phase it names never started.
- `abandon NUMBER [--reason TEXT]` — park a phase that will not resume. It writes the residue the selector reads to skip and report the phase, releases the board group, and keeps the run directory.
- `--session <NUMBER> <PHASE_FILE>` — run one phase inside tmux. It holds a live claim before the work starts: a pane opened by a tick verifies the fence it was given, and a session started by hand claims the phase, plus its declared group, itself. A session the board refuses exits non-zero and opens no window.

With no mode flag, a tick runs once.

## Stop, resume, and claim expiry

`--stop` writes a halt marker and holds the recorded claim for an explicit `--resume [NUMBER]` or `--release`. Clearing the halt marker alone leaves that hold in place, until the claim's own heartbeat window passes: the hold is not renewed, so a tick drops it once the claim has expired and the machine no longer owns the work.

A resume keeps the halt marker, so it starts one phase while later ticks stay halted. `--release` clears the hold whether or not the board write lands.

`--clear-halt` (`make clear-halt`) removes the halt and stall markers. The halt record beside them, `halted.json`, is removed with them only when the phase it names never started: its run directory holds neither `run.json` nor `resume.json`. That phase cannot resume, so clearing it is the only way forward, and the next tick starts it. When either file is present the record stays and the line stays halted, because a resumable run must not lose its halt; a record that cannot be read is kept too. The command prints each path it removed. It removes no run directory, log, or reservation.

Stop and resume can overlap. A resume accepts a matching stop hold just before it opens the phase window. A stop that reaches this point first may be accepted by that explicit resume.

A stop written after the accepted hold is removed stays in place. The halt marker remains in both cases. To stop a resume that is still starting, run `--stop` again once its phase window is open.

A hold controls local launches. Board ownership still uses the claim lease. Ticks renew active work even when the line is halted.

An idle halted or manually stopped line does not renew its claim. The claim can expire, wait through the board cooldown, and pass to another owner.

A later resume must renew the old fence or take a fresh reservation. It refuses when another owner holds the work.

## Scan for uncovered work

`python -m argus.board.reservations scan --config <cfg> [--phase N] [--machine-id ID]` answers one question: which phase run directories hold work that no live claim covers. It reads the board and writes nothing.

It reports a phase with run work but no claim record, an expired claim, a claim held by another machine, or a terminal record, each with its reason:

```json
{"state": "OK", "phases": [{"phase": "100002", "machine_id": null, "status": null, "reason": "no_claim"}]}
```

Exit 0 means the board was read. Exit 2 means the board or a flag was not usable, and the output names the error.

Run it before the first edit of an interactive phase, and when the board looks free but a run directory holds work.

## Failed jobs

A glide job that fails is retried with capped exponential backoff and full jitter, then recorded as `failed.json` beside the phase's run directory; the selector skips that phase, so the line continues to the next job instead of halting. `ARGUS_JOB_RETRIES` sets the retry count, 3 by default, so a job is tried 4 times. A failure that exhausts its retries sends a `failed` notification naming the residue.

## CI repair rounds

Each tick, before new work, the driver reads the checks of every phase pull request that waits on review, and of every open pull request of `issues` work. A pull request with a failed check gets a repair round: one run of `repair.pipeline` in the phase window, so one active run stays the rule. The tick that starts a round starts no new work. A round is counted before its window opens, so a launch that fails still uses one attempt; a round that cannot be counted never launches. Rounds start now, after `repair.wait` seconds, and after twice that; after `repair.attempts` rounds a check that is still red sends one Needs You notice. After that notice Argus starts no further round for that head of the pull request and sends no notice, even when its checks turn red again later. The budget belongs to one head commit: a new head pushed to the branch starts a fresh budget of `repair.attempts` rounds. The attempt record in the run folder keeps the head and the count, and the next head replaces it. A checks read that fails, a head read that fails, or a pull request listing that fails ends the tick before new work, and the next tick reads again. While a phase window runs, the tick still reads the checks and records state, but starts no round. A check that stays pending for more than 30 minutes on one head sends one Needs You notice for that head; the notice clears once no check is pending, and no repeat notice goes out for that head. An attempt record that cannot be read sends one Needs You notice, and no round starts for that pull request until the file is moved aside.

A round runs in its own git worktree, `repair-worktree-<pr>` in the driver state folder, detached at the fetched pull request branch. The main checkout never moves. When the driver's runs folder is inside the checkout, the worktree gets a link to that folder's top directory, so the pipeline's run folder and the files it reads there resolve. A folder rule such as `private/` does not match a link, so when the checkout ignores that folder the driver adds a `/<folder>` line to the checkout's `.git/info/exclude`. When Git still does not ignore the link, the round does not start and a notice says why. When the run made a commit, the session pushes it to the pull request branch, never with force, so a branch that moved rejects the push. The session then removes the worktree. A worktree that a crash left behind is removed on the next tick. Before any removal, work that was not pushed is saved: uncommitted edits become one commit on top of the round's commits, and a ref `refs/argus/repair-backup/<pr>-<time>` keeps it. A notice names the ref and its commit. Argus never pushes or deletes that ref; inspect it with `git log <ref>`, and delete it with `git update-ref -d <ref>` when you no longer need it. When the backup fails, the worktree stays, a notice names it, and the next tick tries again. `--merge-check-only` starts no round, and neither does a halted line or an unreadable `repair.pipeline`. With `issues` work, the driver reads only pull requests on a branch that an issue run folder records: the claim's reservation and its frozen task name that branch. `gh pr list --head` matches the branch name only, so the driver keeps a pull request only when its head is that branch in this repository; a fork pull request with the same branch name is ignored, and the driver logs one line for it. A run folder without that record is skipped, and a pull request list that `gh` cannot read is retried on the next tick. The round's `glide` gets `ARGUS_CONFIG_FILE`, the absolute path of the configuration file the driver loaded, so the pipeline's hooks read the same settings.

## Two drivers on one machine

Two drivers must never share one checkout, the path in `repo-root`. A tick of one driver can switch the branch of that checkout, or commit leftover work on its current branch, while the other driver's agent edits the same files.

Argus enforces this. A phase holds an exclusive lock on the checkout for its whole run. The lock file is `argus-driver-checkout.lock` in the repository's Git common directory, so every worktree of that repository shares it. A tick of another driver refuses before it cuts a branch, and `--check` reports the holder. The refusal names the holding config's file name, and the one action: give each driver its own checkout. The per-config lock does not cover this. It covers one config's run folder and tick. The OS releases the checkout lock when the phase exits, so a crash does not leave it held.

Give each driver its own clone of the repository, its own `driver.runs` and `driver.state`, and its own `tmux.session`. The steps for an issue driver host are in [Set up an issue driver host](issue-driver-setup.md). For example, run each driver under its own Linux user or on its own machine. The example configurations use the same `repo-root` and the same tmux session, so change both before you run a second driver from them.

## The Configuration File

`--config` names the file. Without the flag, Argus uses `config.json` in the current folder when it is there. `--self-test` reads no file.

Four flags override the file for one run: `--driver`, `--repo-root`, `--phases-directory`, and repeatable `--phase-glob`. The readiness report prints which of the flag and the file supplied each value. [configuration.md](configuration.md) covers the values.
