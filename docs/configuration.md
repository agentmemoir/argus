# Configuration

A tick reads one JSONC file. Pass its path with `--config`, or leave the flag out and put `config.json` in the current folder. `config.sample.jsonc` is the schema, with a comment on each value.

The file's rules:

- Comments are allowed; everything else is strict JSON, so there are no trailing commas.
- No value has a default in code: a missing required key stops the tick and names the key.
- No environment variable is read.
- A relative path resolves against the folder that holds the file itself, not the working directory and not the project folder. An absolute path is used as written.
- A command-line flag beats the file for one run. A relative path given by a flag resolves against the working directory, like any command-line path.

## The Values

- `repo-root` — the project checkout. Required.
- `pipeline` — the pipeline file a tick runs. Required.
- `phases` — the phases repository the work-item files live in: `repo` (a separate checkout), `directory` (a folder inside it), `branch`, and `remote`, where Argus commits and pushes what it writes. Required for the phase source. The checkout must be its own repository, with a Git destination different from the public `repo-root`'s. The board and the phases are both private: they may share one Git destination, so one private checkout can hold both.
- `number-input`, `file-input` — the pipeline input names that carry the work-item number and file. Optional, but a pipeline that declares those inputs needs them named.
- `driver.provider` — the work source. Required. An `issues` block that holds only the trust keys (`trusted-authors`, `trusted-apps`, `trusted-associations`, `codeowners-file`, `resolve-teams`, `comment-trust`, `untrusted-comments`) is allowed for any provider; the issue-source keys still require the `issues` provider.
- `driver.include` — the work-item file globs. Required. `driver.exclude` — file names to ignore. Optional.
- `driver.runs` — the run-state folder, holding session logs, the ledger, and the receipts. Required unless `board` is set. With a board, it defaults to a `runs` folder inside `board.repo`, and a folder outside the board checkout is refused by name. The folder must exist before a tick starts.
- `driver.state` — the folder inside `driver.runs` that holds the driver's own log, lock, halt marker, and pid file. Required. A name, not a path.
- `driver.stale-after` — seconds without agent output before a run counts as stalled. Required.
- `driver.outage-stale` — the longer bound used when a merge check could not answer at all, so a host outage is not reported as a stalled phase. Required.
- `driver.quiet-minutes` — the notification quiet window, in minutes. Required.
- `driver.keep-logs-days` — session logs older than this many days are deleted. Required.
- `driver.merge-check-max` — how many published phases one tick checks for a merge before it starts more work. Required.
- `driver.recover-own-claim` — whether a tick may take back this host's own claim when its run died. Required.
- `machine.id-file` — a file whose first non-empty line is the machine's name, recorded as the owner of a claim. Required. A relative path resolves against the config file, so each machine keeps its own file while the rest of the configuration is shared.
- `machine.settings-file` — an overlay file this machine loads before the flags. Optional. See [Overrides](#overrides).
- `commands.glide`, `commands.git`, `commands.tmux`, `commands.python`, `commands.hermes`, `commands.gh` — the external commands, named rather than searched for. Required.
- `commands.reply-check` — a command that reads a draft pull request reply on stdin and exits non-zero to refuse it. Optional; an empty string disables the check.
- `commands.hooks-install` — the argument list that installs this project's git hooks in the work checkout, for example `["make", "hooks"]`. A list, never a shell string. Optional, with no default. Argus runs it only when the pipeline's `pre_run` hook refuses the checkout, and it first copies the checkout's hooks folder into the run state. Without it, a refused checkout is reported as Needs You.
- `tmux.session`, `tmux.width`, `tmux.height`, `tmux.idle-window`, `tmux.phase-window`, `tmux.idle-script`, `tmux.refresh` — the tmux session. `tmux.width` and `tmux.height` are the default size used when no client is attached; windows otherwise follow the attached terminal. Required.
- `board.repo`, `board.file`, `board.branch`, `board.remote` — the shared board. Required only when the `board` object is present. `board.backup-branch` — names the backup ref that mirrors the board branch; optional, and defaults to `<board.branch>--argus-backup`.
- `runstate.repo`, `runstate.remote`, `runstate.branch` — where run state is committed and confirmed: the checkout that holds `driver.runs`, the remote to push and confirm against, and the integration branch carrying the receipt. Optional as a whole; when absent Argus derives the checkout from the run directory and resolves the remote on that checkout. Required only when the `runstate` object is present.
- `notify.profile`, `notify.channel`, `notify.disabled` — the notification target. Required when the `notify` object is present. `notify.fallback`, `notify.heartbeat` — optional.
- `repair.pipeline`, `repair.attempts`, `repair.wait` — the CI repair round for a red check on an open driver pull request. `pipeline` is the one-step repair pipeline; the default is `repair.yaml` beside `pipeline`. `attempts` is the number of rounds before Needs You; the default is 3. `wait` is the seconds added before each later round; the default is 600, so the rounds start now, after 10 minutes, and after 20 minutes. Optional. The driver receives them as `REPAIR_PIPELINE`, `REPAIR_ATTEMPTS`, and `REPAIR_WAIT`. [CI repair rounds](running.md#ci-repair-rounds) describes when a round starts and where it runs.
- `estimate.factors-file`, `estimate.ideal-days` — the estimate factor: both keys switch it on. `factors-file` is a JSON file inside a git checkout with an upstream, holding `{"factors": {"<harness id>": 1.0}}`; a harness with no factor starts from the slowest one in the file. `ideal-days` runs from `repo-root` and prints one phase's ideal days, with the phase number added as its last argument; no number, no update. Optional. After a phase finishes from a fresh start, the driver moves the developer harness's factor toward phase hours / (ideal days x 2), by at most 20% per phase, then commits and pushes the file alone. A resumed run is skipped.

The notification values belong to whoever receives the notices, so a configuration file that holds them must not be committed to a public tree.

## Overrides

The four flags `--driver`, `--repo-root`, `--phases-directory`, and repeatable `--phase-glob` beat the file for one run. The readiness report prints every value with the flag-or-file source it came from.

`machine.settings-file` loads one JSONC overlay before those flags. It may replace only keys already present in the base file. Paths from the overlay resolve against its own folder. The report marks these values as `overlay`.

## Worked Sample

[`examples/argus-config.jsonc`](../examples/argus-config.jsonc) is a filled-in configuration, and [`examples/project/`](../examples/project/) is the small project it points at. [`examples/issue-config.jsonc`](../examples/issue-config.jsonc) shows the issue source. [Issues](issues.md) lists its keys, trust rules, separate board file, and private task path.
