# Set up an issue driver host

This page sets up one host that runs an issue driver. Do these steps once for each host, before the first tick. For how claims and launch faults behave, read [GitHub issue work](issues.md).

You need Bash, Git, tmux, Python with PyYAML, and a configured `gh`. [Running Argus](running.md#install) shows how to install the command.

## 1. Give the driver its own user or machine

Run each issue driver under its own Linux user, or on its own machine. Two drivers must never share one checkout. [Two drivers on one machine](running.md#two-drivers-on-one-machine) explains why.

Replace each `<placeholder>` in these steps with your own value.

## 2. Clone the work checkout

```sh
git clone <repo-url> <work-checkout>
```

Set `repo-root` to `<work-checkout>` in the configuration file. Give this driver its own `driver.runs`, `driver.state`, and `tmux.session`. No other driver may use them. [Configuration](configuration.md) lists these keys.

## 3. Create the board checkout and the run folder

Clone the private board repository to `<board-checkout>`. Set `board.repo` to that checkout. Argus never makes the board checkout itself, so clone it first.

Argus makes the run-state folder and the driver's state folder when they are missing, and it names each one in a line. Leave `driver.runs` unset to use a `runs` folder inside the board checkout. If you set it, the folder must be inside that checkout, and the configuration is refused by name when it is not.

## 4. Check the temporary folder

Argus runs each Glide stage with `TMPDIR` and `BUN_TMPDIR` set to a stage folder under the run state. You do not set either variable. `--check` creates that folder and starts a file in it. If the folder refuses to run files, `--check` fails and names the cause and the action.

## 5. Install the git hooks in the work checkout

The pipeline's `pre_run` hook refuses a run while the work checkout's git hooks are missing or stale. Argus runs that hook before each Glide launch and in `--check`. When the hook refuses, Argus runs the install command that you set as `commands.hooks-install`. That command is an argument list, for example `["make", "hooks"]`, and it is never a shell string. Before the command runs, Argus copies the checkout's hooks folder into the run state, and it writes one line that names the command and the backup.

Set `commands.hooks-install` once. If it is not set, Argus cannot install the hooks. The launch stops, and Needs You names the key. Set the key, then run `argus --check` again. [Launch faults](issues.md#launch-faults) describes what Argus does next when a launch stops.

## 6. Set up the claims board

Set `board.repo`, `board.file`, `board.branch`, and `board.issues-file` in the configuration file. The first claim creates the board branch through the normal write path, so you do not create it by hand. A remote that holds neither the board branch nor its backup has never had a board, and it reads as empty. A board whose backup remains but whose branch is gone has been deleted, so Argus stops with an error until the branch is restored. [Board branch backup and recovery](paths-and-ownership.md#board-branch-backup-and-recovery) explains the backup branch and how to restore it.

## 7. Run the tick from cron with `flock`

Run one tick every few minutes. Argus never edits a crontab, so add the line yourself. `--check` reads the crontab with `crontab -l`, read only, and says whether a line runs this configuration. When none does, it prints the exact line to add with `crontab -e`, and the check that proves the line is in place.

The line has this shape. Use the line that `--check` prints for this host, because it has the full paths.

```cron
*/5 * * * * flock -n <state-dir>/cron.lock <argus> --config <config-file>
```

`flock` is optional. `--check` leaves it out of the line when `flock` is not installed, and the driver's tmux check still guards against a second start. The configuration file names the commands that Argus runs, so cron needs no other `PATH` entry for them.

## 8. Make the credential logins yourself

Log in to GitHub, and to any other service the pipeline needs, as the person who runs this host. Do not let an agent make these logins.

## 9. Check the setup

Run these commands in this order.

```sh
argus --config <config-file> --check
argus --config <config-file> --dry-run
```

`--check` makes the git hooks ready, runs the `pre_run` hook, and reports whether a cron line runs this configuration, so a hook refusal shows before the first tick. A missing cron line does not fail `--check`, but it is reported as Needs You with the line to add. When both commands pass, the cron job runs the first tick.

## Trade-off: the docker group

Docker group membership gives root-equivalent access to the host. A driver user in that group no longer has the separate-user boundary from step 1. Add the driver user to the `docker` group only when the pipeline needs it, and accept that the boundary is gone.

Next, read [GitHub issue work](issues.md) for claims and launch faults, or [Configuration](configuration.md) for each key.
