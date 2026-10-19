# Running Argus

One configuration file per tick, and one command. The file names every value the tick uses, including the commands it runs.

Argus needs Bash, Git, tmux, and a Python with PyYAML. The publisher uses the configured `gh`; notifications are optional.

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

- `--check` — the readiness report. It prints every configured value with the flag-or-file source it came from, checks the commands and folders the tick will use, runs Argus's own self-tests, and exits nonzero when it finds a problem.
- `--dry-run` — what the tick would do, without launching anything.
- `--merge-check-only` — settle phase merges and group readiness without selecting or launching work.
- `--self-test` — the driver's own test. It needs no configuration file and starts no agent.
- `--stop` — halt the live run.
- `--release` — stop the live run and give its reservation group back.
- `--clear-halt` — remove the halt and stall markers, and the halt record when the phase it names never started.
- `abandon NUMBER [--reason TEXT]` — park a phase that will not resume. It writes the residue the selector reads to skip and report the phase, releases the board group, and keeps the run directory.
- `--session <NUMBER> <PHASE_FILE>` — run one phase inside tmux.

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

## Failed jobs

A glide job that fails is retried with capped exponential backoff and full jitter, then recorded as `failed.json` beside the phase's run directory; the selector skips that phase, so the line continues to the next job instead of halting. `ARGUS_JOB_RETRIES` sets the retry count, 3 by default, so a job is tried 4 times. A failure that exhausts its retries sends a `failed` notification naming the residue.

## The Configuration File

`--config` names the file. Without the flag, Argus uses `config.json` in the current folder when it is there. `--self-test` reads no file.

Four flags override the file for one run: `--driver`, `--repo-root`, `--phases-directory`, and repeatable `--phase-glob`. The readiness report prints which of the flag and the file supplied each value. [configuration.md](configuration.md) covers the values.
