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

Clone the private board repository to `<board-checkout>`. Set `board.repo` to that checkout.

Create the run folder before the first tick. A tick does not start while it is missing. Leave `driver.runs` unset to use a `runs` folder inside the board checkout. If you set it, the folder must be inside that checkout, and `--check` names the key when it is not.

## 4. Check the temporary folder

Argus runs each Glide stage with `TMPDIR` and `BUN_TMPDIR` set to a stage folder under the run state. You do not set either variable. `--check` creates that folder and starts a file in it. If the folder refuses to run files, `--check` fails and names the cause and the action.

## 5. Install the git hooks in the work checkout

Install the project's git hooks in `<work-checkout>` before the first tick. If the pipeline's `pre_run` hook checks for them and finds them missing, it refuses the run. That refusal is a launch fault. [Launch faults](issues.md#launch-faults) describes what Argus does next.

## 6. Set up the claims board

Set `board.repo`, `board.file`, `board.branch`, and `board.issues-file` in the configuration file. Argus creates the board branch on its first write. [Board branch backup and recovery](paths-and-ownership.md#board-branch-backup-and-recovery) explains the backup branch and how to restore it.

## 7. Run the tick from cron with `flock`

Run one tick every few minutes. Wrap the command in `flock -n`, so a new tick does not start while the last one still runs.

```cron
*/5 * * * * flock -n <state-dir>/cron.lock argus --config <config-file>
```

Use the full path to `argus` if cron cannot find it. The configuration file names the commands that Argus runs, so cron needs no other `PATH` entry for them.

## 8. Make the credential logins yourself

Log in to GitHub, and to any other service the pipeline needs, as the person who runs this host. Do not let an agent make these logins.

## 9. Check the setup

Run these commands in this order.

```sh
argus --config <config-file> --check
argus --config <config-file> --dry-run
```

`--check` runs the `pre_run` hook, so a hook refusal shows before the first tick. When both commands pass, the cron job runs the first tick.

## Trade-off: the docker group

Docker group membership gives root-equivalent access to the host. A driver user in that group no longer has the separate-user boundary from step 1. Add the driver user to the `docker` group only when the pipeline needs it, and accept that the boundary is gone.

Next, read [GitHub issue work](issues.md) for claims and launch faults, or [Configuration](configuration.md) for each key.
