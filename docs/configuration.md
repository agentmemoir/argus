# Configuration

A tick reads one JSONC file. Pass its path with `--config`, or leave the flag out and put `config.json` in the current folder. `config.sample.jsonc` is the schema, with a comment on each value.

The file's rules:

- Comments are allowed; everything else is strict JSON, so there are no trailing commas.
- No value has a default in code: a missing required key stops the tick and names the key.
- No environment variable is read.
- A relative path resolves against the folder that holds the file itself, not the working directory and not the project folder. An absolute path is used as written.
- A command-line flag beats the file for one run.

## The Values

- `repo-root` — the project checkout. Required.
- `pipeline` — the pipeline file a tick runs. Required.
- `roadmap-dir` — the folder holding the work-item files. Required.
- `number-input`, `file-input` — the pipeline input names that carry the work-item number and file. Optional, but a pipeline that declares those inputs needs them named.
- `driver.provider` — the work source. Required.
- `driver.include` — the work-item file globs. Required. `driver.exclude` — file names to ignore. Optional.
- `driver.runs` — the run-state folder, holding session logs, the ledger, and the receipts. Required. The folder must exist before a tick starts.
- `driver.state` — the folder inside `driver.runs` that holds the driver's own log, lock, halt marker, and pid file. Required. A name, not a path.
- `driver.stale-after` — seconds without agent output before a run counts as stalled. Required.
- `driver.outage-stale` — the longer bound used when a merge check could not answer at all, so a host outage is not reported as a stalled phase. Required.
- `driver.quiet-minutes` — the notification quiet window, in minutes. Required.
- `driver.keep-logs-days` — session logs older than this many days are deleted. Required.
- `driver.merge-check-max` — how many published phases one tick checks for a merge before it starts more work. Required.
- `driver.recover-own-claim` — whether a tick may take back this host's own claim when its run died. Required.
- `machine.id-file` — a file whose first non-empty line is the machine's name, recorded as the owner of a claim. Required. A relative path resolves against the config file, so each machine keeps its own file while the rest of the configuration is shared.
- `commands.glide`, `commands.git`, `commands.tmux`, `commands.python`, `commands.hermes`, `commands.gh` — the external commands, named rather than searched for. Required.
- `tmux.session`, `tmux.width`, `tmux.height`, `tmux.idle-window`, `tmux.phase-window`, `tmux.idle-script`, `tmux.refresh` — the tmux session and its geometry. Required.
- `board.repo`, `board.file`, `board.branch`, `board.remote` — the shared board. Required only when the `board` object is present.
- `notify.profile`, `notify.channel`, `notify.disabled` — the notification target. Required when the `notify` object is present. `notify.fallback`, `notify.heartbeat` — optional.

The notification values belong to whoever receives the notices, so a configuration file that holds them must not be committed to a public tree.

## Overrides

The four flags `--driver`, `--repo-root`, `--roadmap-dir`, and repeatable `--phase-glob` beat the file for one run. The readiness report prints every value with the flag-or-file source it came from.

## Worked Sample

[`examples/argus-config.jsonc`](../examples/argus-config.jsonc) is a filled-in configuration, and [`examples/project/`](../examples/project/) is the small project it points at.
