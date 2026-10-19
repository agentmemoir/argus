#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Argus: the unattended driver. Its source folder holds the driver and the
# shell helpers it sources. The pipeline, the stages and the workers are
# project content and stay in the workflow folder; this driver reaches them
# only by path, never by walking into them.
#
# Name a foreign project with --repo-root. The argus command calls this file.
#
#   phase-driver.sh [--dry-run]                  # cron entrypoint
#   phase-driver.sh --check                      # report environment readiness
#   phase-driver.sh --merge-check-only           # settle publication without new work
#   phase-driver.sh --self-test                  # hermetic guard/halt/notify test
#   phase-driver.sh --stop                       # stop the live run (halt + Ctrl-C)
#   phase-driver.sh --release                    # stop the live run and give its group back
#   phase-driver.sh --resume [<N>]               # resume the halted phase at its stage
#   phase-driver.sh abandon <N> [--reason TEXT]  # park a phase that will not resume
#   phase-driver.sh --session <N> <phase-file>   # one phase, run inside tmux
#
# The cron tick reserves the selected phase in the private coordination branch
# before tmux starts (a fetch/push failure is fail-closed); `--session` starts no reservation and writes no claim.
#
# Cron runs the driver every few minutes. It does nothing while a phase is
# running; while halted it settles publication only. Otherwise it starts the lowest
# uncompleted phase in the tmux session named below. A phase that ends
# blocked/rejected/errored, OR that is stopped by a signal (Ctrl-C / tmux
# kill-session), writes a halt marker and stops the line until the operator
# clears it. The one exception is a remedy loop that ran out of rounds with
# findings open: it ends remedy_escalated, records the residue, releases its
# claim, and the line continues. Start/end/halt notifications go to Discord
# through the Hermes profile below.
#
# Every value the tick uses comes from one configuration file, named with
# --config or found at ./config.json. No value is read from the environment or
# from a machine-local file, and no value has a default in code: a missing
# required key stops the tick and names the key. The four flags --driver,
# --repo-root, --phases-directory and --phase-glob override the file for one run,
# and --check prints every value with the flag-or-file source it came from.
#
# The selector's own refusals are recorded three ways, one for each reader:
# selection.err in the configured driver-state folder holds the raw text, driver.log holds it under a
# `selector: ` prefix so a line the driver wrote is distinguishable from one the
# selector wrote, and stderr still carries the raw text, which is what the cron
# log captured before any of this existed. The stderr write is additive, so an
# unconfigured machine sees the same bytes in the same log as it always did.
#
# The tmux session is persistent: it is created once and outlives every phase.
# It holds an `idle` window (status, created once) and a `phase` window (created
# per launch, closes itself when the phase ends). "Busy" therefore means the
# phase window exists, NOT that the session exists. Attach a display with
# `tmux attach -t development -r`; -r is read-only and ignores the client's
# size, so the display cannot resize the agent's pane.
#
# The launch is opened with the repository root as its working directory and
# given a phase file path relative to it, so a phases folder outside this
# checkout cannot be launched from here. The folder is still read, and the
# selector reports such a file as an absolute path; this is where that stops.
# A machine that needs it has to run the driver from that checkout.


# All tmux window handling lives in argus/driver/phase-tmux.sh.
#
# Local test hooks (never set these in cron):
#   DISABLE_NOTIFY=1      log notifications instead of sending them
#   DRIVER_STUB_RC=<n>    skip glide; pretend it exited with rc <n>
#   DRIVER_STUB_SLEEP=<s> how long the stub "works" (default 2)
#   DRIVER_RUN_DIR=<dir>  isolate runtime state (used by --self-test)
#   DRIVER_STUB_BOARD_FENCE=<renew|reserve|fail>  the resume's board fence
set -euo pipefail

# Parse the mode flags before reading the configuration file. The four layout
# flags are one-run overrides of the file; --config names the file.
CLI_DRIVER=""; CLI_REPO=""; CLI_PHASES=""; CLI_GLOB=(); CLI_CONFIG=""
MODE_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --driver|--repo-root|--phases-directory|--phase-glob|--config)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'argus: %s needs a value\n' "$1" >&2; exit 2; }
      case "$1" in
        --driver) CLI_DRIVER="$2" ;;
        --repo-root) CLI_REPO="$2" ;;
        --phases-directory) CLI_PHASES="$2" ;;
        --phase-glob) CLI_GLOB+=("$2") ;;
        --config) CLI_CONFIG="$2" ;;
      esac
      shift 2 ;;
    --help|-h)
      echo 'usage: argus [--config FILE] [--driver phase] [--repo-root PATH] [--phases-directory PATH] [--phase-glob GLOB] [--dry-run|--check|--merge-check-only|--self-test|--stop|--release]'
      echo '       argus --session NUMBER PHASE_FILE'
      echo '       argus --resume [NUMBER]'
      echo '       argus --clear-halt'
      echo '       argus abandon NUMBER [--reason TEXT]'
      echo 'A tick reads one configuration file: --config FILE, or ./config.json when it is there.'
      echo 'The file names every value the tick uses, including the pipeline and the board checkout;'
      echo 'config.sample.jsonc is the schema, and no value has a default. --self-test needs no file.'
      echo '--merge-check-only settles phase merges and group readiness without selecting or launching work.'
      echo 'Maintenance waits while a phase window or an orphaned phase process is active.'
      echo '--stop holds a claim for explicit --resume or --release; --resume keeps the line halted.'
      exit 0 ;;
    *) MODE_ARGS+=("$1"); shift ;;
  esac
done
set -- "${MODE_ARGS[@]+"${MODE_ARGS[@]}"}"

_SCRIPT_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SCRIPT_SRC" ]; do
  _link="$(readlink "$_SCRIPT_SRC")"
  case "$_link" in
    /*) _SCRIPT_SRC="$_link" ;;
    *) _SCRIPT_SRC="$(dirname "$_SCRIPT_SRC")/$_link" ;;
  esac
done
ARGUS_DIR="$(cd "$(dirname "$_SCRIPT_SRC")" && pwd)"
# The driver and its shell helpers live in `driver/`; the Python tools it runs
# live in the package folder above it and in the role packages under it.
ARGUS_PKG="$(cd "$ARGUS_DIR/.." && pwd)"
ARGUS_DRIVER="$ARGUS_DIR"
ARGUS="$ARGUS_PKG"
SELF="$ARGUS_DIR/$(basename "$_SCRIPT_SRC")"
ARGUS_CONFIG_ARGS=()
ARGUS_CONFIG_VARS=(
  REPO PIPELINE PHASES_REPO PHASES_DIRECTORY PHASES_BRANCH PHASES_REMOTE PHASE_NUMBER_INPUT PHASE_FILE_INPUT DRIVER
  PHASE_INCLUDE PHASE_EXCLUDE WF DRIVER_STATE STALE_AFTER_SEC OUTAGE_STALE_SEC
  QUIET_MINUTES LOG_KEEP_DAYS MERGE_CHECK_MAX RECOVER_OWN_CLAIM PHASE_MACHINE_ID
  CMD_GLIDE CMD_GIT CMD_TMUX CMD_PYTHON CMD_HERMES CMD_GH SESSION_NAME
  TMUX_WIDTH TMUX_HEIGHT TMUX_IDLE_WINDOW TMUX_PHASE_WINDOW TMUX_IDLE_SCRIPT
  TMUX_REFRESH BOARD_REPO BOARD_FILE BOARD_BRANCH BOARD_BACKUP_BRANCH BOARD_REMOTE
  HERMES_PROFILE_NAME DISCORD_CHANNEL NOTIFY_DISABLED DISCORD_FALLBACK_CHANNEL
  HEARTBEAT_URL REPAIR_PIPELINE REPAIR_ATTEMPTS REPAIR_WAIT
  REPAIR_VERIFY REPAIR_VERIFY_TIMEOUT REPAIR_STALE_AFTER
)

# Argus's own shell helpers, sourced from beside this file. The folder is
# computed from this script's own position rather than from the layout, because
# these files are how the driver runs at all: a folder named in configuration
# that resolves to nothing would otherwise take the whole tick down with a
# "No such file" from bash, which names a line of this script rather than the
# setting that is wrong.
#
# The driver's own home is its own position, always: the helpers beside this file
# are how the driver selects, claims and reports, so a machine that configured a
# different folder for them would be configuring itself not to run. The Python
# tools live one level up, in the role packages named by `ARGUS`.
NOTIFIER="$ARGUS/notify/notifications.py"
for helper in \
  phase-tmux.sh lib-stat.sh \
  lib-driver-config.sh lib-driver-log.sh lib-driver-notify.sh \
  lib-driver-run.sh lib-driver-job.sh lib-driver-select.sh lib-driver-check.sh \
  lib-driver-drive.sh lib-driver-dryrun.sh lib-driver-select-flow.sh \
  lib-driver-select-stub.sh lib-driver-select-real.sh lib-driver-maintenance.sh lib-driver-launch.sh \
  lib-driver-halt-record.sh lib-driver-fence.sh \
  lib-driver-resume.sh lib-driver-abandon.sh lib-driver-estimate.sh \
  lib-selftest.sh lib-selftest-launch.sh lib-selftest-flow.sh \
  lib-selftest-unavailable.sh lib-selftest-reasons.sh lib-selftest-recovery.sh \
  lib-selftest-merge.sh lib-selftest-stranding.sh lib-selftest-group.sh \
  lib-selftest-resume.sh lib-selftest-resume-guards.sh \
  lib-selftest-abandon.sh lib-selftest-clear-halt.sh lib-selftest-job.sh \
  lib-selftest-fixture.sh selftest-support.sh; do
  if [ ! -r "$ARGUS_DIR/$helper" ]; then
    printf 'argus: cannot read %s: this driver is broken, not misconfigured\n' \
      "$ARGUS_DIR/$helper" >&2
    exit 3
  fi
done

# Configuration: schema loading, path resolution, and startup (argus_init). The
# module defines argus_init, which the entrypoint calls below.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-config.sh"

. "$ARGUS_DIR/lib-driver-issues.sh"
# Timestamps, logging, and the log-line sanitising helpers.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-log.sh"
# Notifications: Discord/Hermes formatting and the branch-state lines.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-notify.sh"
# Run state: sessions, stall checks, escalation, and the phase run directory.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-run.sh"
# One job: retry a failed run, and record a failure that outlasts the retries.
. "$ARGUS_DIR/lib-driver-job.sh"
# The estimate factor update a finished phase makes.
. "$ARGUS_DIR/lib-driver-estimate.sh"
# The work selector, reservation results, and the merge check.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-select.sh"
. "$ARGUS_DIR/lib-driver-maintenance.sh"
# The reservation group: its member array, its one branch, and its renewal.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-group.sh"
# The readiness report (`--check`).
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-check.sh"
# The tick: reserve, branch, and launch.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-drive.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-dryrun.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-select-flow.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-select-stub.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-select-real.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-launch.sh"
# The halt record, the board fence, the resume, and the
# abandon close-out.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-halt-record.sh"
# shellcheck disable=SC1091
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-fence.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-resume.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-driver-abandon.sh"
# The self-test and its case stages.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-recover.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-launch.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-flow.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-unavailable.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-reasons.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-recovery.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-merge.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-stranding.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-group.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-resume.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-resume-guards.sh"
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-abandon.sh"
. "$ARGUS_DIR/lib-selftest-clear-halt.sh"
. "$ARGUS_DIR/lib-selftest-job.sh"

argus_init "${1:-}"

# Persistent tmux session: idle + phase windows. Sourced after the configuration
# is loaded because the file reads the tmux command and its geometry at source
# time.
# shellcheck disable=SC1091
. "$ARGUS_DIR/phase-tmux.sh"
# Portable mtime helpers (BSD stat vs GNU/uutils stat). See lib-stat.sh.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-stat.sh"
# The self-test's throwaway checkout.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-selftest-fixture.sh"
# The self-test's tripwire on the machine's real checkouts and run state. The
# exit trap outlives every function in the driver, so this is sourced rather
# than defined beside the self-test.
# shellcheck disable=SC1091
. "$ARGUS_DIR/selftest-support.sh"

DRY_RUN=0
CUR_PHASE=""
CUR_PHASE_FILE=""
_STOPPED=0
NOTIFY_DEAD=0

# Defaults read by the merge check; see confirm_pending_reviews.
PENDING_PUBLISHED=0
MERGE_CHECK_HEALTH="ok"

case "${1:-}" in
  --session)
    [ "$#" -eq 3 ] || { echo "usage: phase-driver.sh --session <N> <phase-file>" >&2; exit 2; }
    run_session "$2" "$3"
    ;;
  --resume)
    shift
    [ "$#" -le 1 ] || { echo "usage: phase-driver.sh --resume [NUMBER]" >&2; exit 2; }
    resume_cmd "${1:-}"
    ;;
  abandon)
    [ "$#" -ge 2 ] || { echo "usage: phase-driver.sh abandon <NUMBER> [--reason TEXT]" >&2; exit 2; }
    shift
    abandon_cmd "$@"
    ;;
  --clear-halt) clear_halt_cmd ;;
  --check)     check ;;
  --merge-check-only) drive --merge-check-only ;;
  --self-test) driver_self_test ;;
  --stop)      stop ;;
  --release)   release ;;
  --dry-run)   DRY_RUN=1; drive ;;
  ""|--drive)  drive ;;
  *)
    echo "usage: phase-driver.sh [--dry-run|--check|--merge-check-only|--self-test|--stop|--release] | --resume [<N>] | abandon <N> [--reason TEXT] | --session <N> <phase-file>" >&2
    exit 2
    ;;
esac
