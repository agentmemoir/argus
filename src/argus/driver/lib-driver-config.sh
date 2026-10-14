#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Configuration: schema loading, path resolution, and startup for the Argus driver.
# Sourced by phase-driver.sh; not executable on its own.
#
roadmap_dir_is_local() {
  case "$(roadmap_dir)" in
    "$REPO"/*) return 0 ;;
    *) return 1 ;;
  esac
}

clear_config_values() {
  local var
  for var in "${ARGUS_CONFIG_VARS[@]}"; do
    unset "$var" "ARGUS_SOURCE_$var"
  done
}

# A Python is needed before the configuration can be read at all, because
# reading it is itself a Python call. Use the operator's PATH for that first
# read; the file names the command used after it loads.

resolve_python() {
  local candidate
  candidate="$(command -v python3 2>/dev/null || true)"
  [ -n "$candidate" ] && [ -x "$candidate" ] || return 1
  PYTHON="$candidate"
}

# Read the configuration through the module beside this file, imported the same
# way the other tools import the package, so it works whatever the checkout is
# named and whatever the working directory is.
run_config() {
  "$PYTHON" - "$ARGUS_PKG" "$@" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import _bootstrap  # noqa: F401
from argus.config import cli as config
raise SystemExit(config.main(sys.argv[2:]))
PY
}

# Take one rendered configuration into this shell. The module prints `VAR=value`
# lines plus an `ARGUS_SOURCE_<VAR>` per value, so the readiness report can say
# which of the flag and the file supplied each one. A load failure names the
# offending key and stops the tick with the module's own message.
load_config() {
  local out
  if ! out="$(run_config "$@")"; then return 2; fi
  eval "$out"
}

# Pass the same file and one-run overrides to every Argus child. The config is
# the source of settings; this array only carries its path and explicit flags.
set_config_args() {
  ARGUS_CONFIG_ARGS=()
  [ -z "${ARGUS_CONFIG_FILE:-}" ] || ARGUS_CONFIG_ARGS+=(--config "$ARGUS_CONFIG_FILE")
  [ -z "$CLI_REPO" ] || ARGUS_CONFIG_ARGS+=(--repo-root "$CLI_REPO")
  [ -z "$CLI_ROADMAP" ] || ARGUS_CONFIG_ARGS+=(--roadmap-dir "$CLI_ROADMAP")
  for _glob in "${CLI_GLOB[@]+"${CLI_GLOB[@]}"}"; do
    ARGUS_CONFIG_ARGS+=(--phase-glob "$_glob")
  done
  [ -z "$CLI_DRIVER" ] || ARGUS_CONFIG_ARGS+=(--driver "$CLI_DRIVER")
}

load_fixture_config() {
  ARGUS_CONFIG_FILE="$1"
  clear_config_values
  load_config --config "$ARGUS_CONFIG_FILE" --format shell
  PHASE_COORDINATION_BRANCH="$BOARD_BRANCH"
  set_config_args
}

# A folder named in configuration that is not a readable directory stops the
# tick before the selector runs. The selector refuses it too; this is the same
# refusal earlier and with the folder named, because the alternative is a log
# line about coordination being unavailable several steps later over what is a
# typo in a settings file.
roadmap_dir_readable() {
  [ -d "$(roadmap_dir)" ] && [ -r "$(roadmap_dir)" ]
}

# Whether this machine's roadmap folder can be used for a launch, and why not
# when it cannot. One answer for the dry run and the tick, so the two cannot
# disagree about whether a folder is launchable.
#
# Asked before the selector runs, and that ordering is the whole point. Both
# refusals arrive from the selector as an empty result with exit 2, which this
# script reads as "nothing to do", and by the time they arrive a phase may
# already have been claimed on the shared board. A claim taken by a tick that
# then refuses is a claim the line cannot clear on its own: every later tick
# reads it back as WAIT_FOR_CLAIM and exits 0, so the line stops silently and
# only an operator takeover can restart it. Asking here costs two filesystem
# checks and cannot strand anything.

work_preflight() {
  PREFLIGHT_REASON=""
  # A missing private folder is not refused here. This function runs inside
  # fixtures that point the driver at a throwaway root holding only a run-state
  # folder, and refusing would make every one of them test the refusal instead of
  # the fault it exists to reproduce. The folder is required once, at startup,
  # before any of this; a machine whose folder is wrong never reaches a tick.
  if [ ! -r "$ARGUS/gitsync/main.py" ]; then
    PREFLIGHT_REASON="the bundled publishing tool is unreadable at $ARGUS/gitsync/main.py"
    return 1
  fi
  # The pipeline file is checked against this checkout, but only when it is
  # there. A fixture root holds the tools and the run state, not the workflow,
  # and refusing on its absence would be the same mistake as above.
  if [ ! -r "$PIPELINE" ]; then
    PREFLIGHT_REASON="the pipeline file is missing or unreadable at $PIPELINE"
    return 1
  fi
  if ! command -v "$CMD_GLIDE" >/dev/null 2>&1 \
      && { [ ! -f "$CMD_GLIDE" ] || [ ! -x "$CMD_GLIDE" ]; }; then
    PREFLIGHT_REASON="glide is missing ('$CMD_GLIDE'); install glide before starting Argus"
    return 1
  fi
  local pipeline_error
  if ! pipeline_error="$("$PYTHON" - "$ARGUS" "$REPO" "${ARGUS_CONFIG_ARGS[@]}" <<'PY' 2>&1
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[3:])
from argus.conventions import phase_run_dir
try:
    phase_run_dir(sys.argv[2], 0, "phase-000000-preflight.md")
except ValueError as exc:
    print(exc)
    raise SystemExit(2)
PY
  )"; then
    PREFLIGHT_REASON="invalid pipeline configuration: $pipeline_error"
    return 1
  fi
  if [ "${WORK_SOURCE:-roadmap}" = issues ]; then
    issue_preflight
    return $?
  fi
  if [ -z "$ROADMAP_DIR" ]; then
    return 0
  fi
  if ! roadmap_dir_readable; then
    PREFLIGHT_REASON="configured roadmap folder $(roadmap_dir) is missing or unreadable"
    return 1
  fi
  if ! roadmap_dir_is_local; then
    PREFLIGHT_REASON="configured roadmap folder $(roadmap_dir) is outside $REPO, so its phase files cannot be launched from this checkout"
    return 1
  fi
  return 0
}

# The roadmap folder this machine reads, as an absolute path. Computed per call
# rather than stored, because REPO is reassigned by the self-test's strand
# fixture and a stored path would keep pointing at the real checkout.
#
# A configured relative path resolves against the repository root, not the
# working directory, for the same reason the selector does it: cron starts the
# driver from an arbitrary directory. Both sides must agree, so both anchor to
# REPO.
roadmap_dir() {
  case "$ROADMAP_DIR" in
    /*) printf '%s' "$ROADMAP_DIR" ;;
    *) printf '%s' "$REPO/$ROADMAP_DIR" ;;
  esac
}

# The same folder as a repository-relative path, which is what a launch needs:
# the phase window is opened with the repository root as its working directory
# and receives a relative phase file. The work source reports launch paths in
# that shape itself, through reported_path, so nothing here has to rebuild one.


argus_init() {
  clear_config_values

  # The self-test builds its own throwaway checkout and its own configuration
  # file, so it must not be made to read this machine's file first. Every other
  # mode takes its whole configuration from the file; nothing here derives a
  # value from the layout or from the caller's environment.
  SELF_TEST_MODE=0
  [ "${1:-}" = "--self-test" ] && SELF_TEST_MODE=1
  if [ "$SELF_TEST_MODE" = 1 ]; then
    CLI_DRIVER=""; CLI_REPO=""; CLI_ROADMAP=""; CLI_GLOB=(); CLI_CONFIG=""
  fi

  # Keep the caller's PATH. The operator installs Glide and chooses Python there.
  export PATH="${PATH:-/usr/bin:/bin}"

  # A Python is needed before the configuration can be read at all, because
  # reading it is itself a Python call. Use the operator's PATH for that first
  # read; the file names the command used after it loads.
  PYTHON=""

  CONFIG_PRECEDENCE='flag > config file'

  if [ "$SELF_TEST_MODE" = 0 ]; then
    resolve_python || PYTHON="python3"
    # A flag path is relative to where the caller stands, and the children start
    # elsewhere (a tmux session starts in the project), so fix it here, as
    # ARGUS_CONFIG_FILE is below.
    case "$CLI_REPO" in ''|/*) : ;; *) CLI_REPO="$PWD/$CLI_REPO" ;; esac
    case "$CLI_ROADMAP" in ''|/*) : ;; *) CLI_ROADMAP="$PWD/$CLI_ROADMAP" ;; esac
    _config_args=()
    [ -z "$CLI_CONFIG" ] || _config_args+=(--config "$CLI_CONFIG")
    _config_args+=(--format shell)
    [ -z "$CLI_REPO" ] || _config_args+=(--repo-root "$CLI_REPO")
    [ -z "$CLI_ROADMAP" ] || _config_args+=(--roadmap-dir "$CLI_ROADMAP")
    for _glob in "${CLI_GLOB[@]+"${CLI_GLOB[@]}"}"; do
      _config_args+=(--phase-glob "$_glob")
    done
    [ -z "$CLI_DRIVER" ] || _config_args+=(--driver "$CLI_DRIVER")
    load_config "${_config_args[@]}" || exit $?
    unset _config_args
    # The file names the Python from here on; the PATH search above only stood in
    # until the file could be read.
    [ -z "${CMD_PYTHON:-}" ] || PYTHON="$CMD_PYTHON"
    ARGUS_CONFIG_FILE="$CLI_CONFIG"
    if [ -z "$ARGUS_CONFIG_FILE" ]; then
      ARGUS_CONFIG_FILE="$PWD/config.json"
    else
      case "$ARGUS_CONFIG_FILE" in /*) : ;; *) ARGUS_CONFIG_FILE="$PWD/$ARGUS_CONFIG_FILE" ;; esac
    fi
    set_config_args
  else
    # The self-test drives itself through shell variables it sets per fixture;
    # only the runner and the external programs are needed before its fixture's
    # configuration file is written and read inside driver_self_test.
    resolve_python || PYTHON="python3"
    CMD_GLIDE=glide
    CMD_GIT=git
    CMD_TMUX=tmux
    CMD_HERMES=hermes
    CMD_GH=gh
    DRIVER=phase
    WORK_SOURCE=roadmap
    PHASE_MACHINE_ID=argus-selftest
    BOARD_DIR=board-data
    ARGUS_CONFIG_FILE=""
    REPO="$(cd "$ARGUS_PKG/../.." && pwd)"
    # The tripwire's baseline reads these before the fixture is built; an empty
    # private checkout and run-state folder are captured as absent, which is what
    # the throwaway run needs so it never reaches a real one.
    WF=""
  fi

  # Every value below comes from the configuration file, or from a flag that beat
  # it for this run. Nothing is read from the environment: the driver sets these
  # variables for its children; it does not take them from a caller.
  case "$DRIVER" in
    phase|roadmap) DRIVER=phase; WORK_SOURCE=roadmap ;;
    issues) DRIVER=issues; WORK_SOURCE=issues ;;
    *) printf 'argus: unknown driver %s; known drivers: phase, issues\n' "$DRIVER" >&2; exit 2 ;;
  esac
  work_kind=phase
  item_key=phase
  BOARD_KIND_ARGS=()
  GITSYNC_KIND_ARGS=()
  if [ "$WORK_SOURCE" = issues ]; then
    work_kind=issue
    item_key=issue
    BOARD_KIND_ARGS=(--issues)
    GITSYNC_KIND_ARGS=(--kind issue)
  fi

  # The board's own branch. The self-test pins each fixture's own branch, so the
  # warning below stays quiet for that invocation rather than printing per case.
  PHASE_COORDINATION_BRANCH="${BOARD_BRANCH:-}"

  if [ "$SELF_TEST_MODE" = 0 ]; then
    WORK_SOURCE_FLAGS=(--source "$WORK_SOURCE")

    # The run-state folder must exist, and is asked for rather than created. A
    # tick that makes the folder it was told to use is not a tick that noticed the
    # folder was wrong: `mkdir -p` on a mistyped path succeeds, and the driver then
    # runs out of a directory it invented while the real one sits unread.
    [ -d "$WF" ] || {
      printf 'argus: run-state folder %s is missing or unreadable\n' "$WF" >&2
      exit 3
    }
    DRIVER_RUN_DIR="$WF/$DRIVER_STATE"
    RUN_DIR="$DRIVER_RUN_DIR"
    LOCK="$RUN_DIR/driver.lock"
    HALT="$RUN_DIR/halted"
    # The halt's own record, written beside the marker at the run-end halt and
    # read by a resume. It outlives the marker: clearing the marker resumes the
    # recorded phase at its stage instead of advancing the queue.
    HALT_RECORD="$RUN_DIR/halted.json"
    STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"
    PIDFILE="$RUN_DIR/pid"
    # The fence the running phase was launched with, so a later `--release`
    # invocation can release the board group without the parent process. And
    # the marker `--release` leaves for the child's signal trap: when it is
    # present, the trap stops the run without writing a halt marker.
    CLAIM="$RUN_DIR/claim"
    RELEASE="$RUN_DIR/release"
    # The reservation group this machine is running, and the one branch its work
    # lives on. Read on every later tick: the renewal needs the group, and the
    # launch needs the branch the earlier tick already cut.
    GROUPFILE="$RUN_DIR/group"
    GROUPBRANCH="$RUN_DIR/group-branch"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"
    LOG="$RUN_DIR/driver.log"

    # A child phase process learns which session it belongs to through
    # CLIO_TMUX_SESSION; prefer that, then the configured session name.
    if [ -n "${SESSION_NAME:-}" ] && [ -n "${CLIO_TMUX_SESSION:-}" ]; then
      SESSION="$CLIO_TMUX_SESSION"
    elif [ -n "${SESSION_NAME:-}" ]; then
      SESSION="$SESSION_NAME"
    else
      SESSION="development"
    fi
    SESSION_NAME="$SESSION"
  fi

  PROFILE="${HERMES_PROFILE_NAME:-}"
  CHANNEL="${DISCORD_CHANNEL:-}"
  FALLBACK="${DISCORD_FALLBACK_CHANNEL:-}"
  HEARTBEAT_URL="${HEARTBEAT_URL:-}"
  NOTIFY_DISABLED="${NOTIFY_DISABLED:-0}"

  PREFLIGHT_REASON=""
}
