#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
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
#   phase-driver.sh --self-test                  # hermetic guard/halt/notify test
#   phase-driver.sh --stop                       # stop the live run (halt + Ctrl-C)
#   phase-driver.sh --session <N> <phase-file>   # one phase, run inside tmux
#
# The server reserves the selected phase in the private coordination branch
# before tmux starts. A coordination fetch/push failure is fail-closed.
#
# Cron runs the driver every few minutes. It does nothing while a phase is
# running or while the line is halted; otherwise it starts the lowest
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
# --repo-root, --roadmap-dir and --phase-glob override the file for one run,
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
# given a phase file path relative to it, so a roadmap folder outside this
# checkout cannot be launched from here. The folder is still read, and the
# selector reports such a file as an absolute path; this is where that stops.
# A machine that needs it has to run the driver from that checkout.
roadmap_dir_is_local() {
  case "$(roadmap_dir)" in
    "$REPO"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# All tmux window handling lives in argus/phase-tmux.sh.
#
# Local test hooks (never set these in cron):
#   DISABLE_NOTIFY=1      log notifications instead of sending them
#   DISABLE_DISCORD=1     alias for disabling Discord sends (same effect today)
#   DRIVER_STUB_RC=<n>    skip glide; pretend it exited with rc <n>
#   DRIVER_STUB_SLEEP=<s> how long the stub "works" (default 2)
#   DRIVER_RUN_DIR=<dir>  isolate runtime state (used by --self-test)
set -euo pipefail

# Parse the mode flags before reading the configuration file. The four layout
# flags are one-run overrides of the file; --config names the file.
CLI_DRIVER=""; CLI_REPO=""; CLI_ROADMAP=""; CLI_GLOB=(); CLI_CONFIG=""
MODE_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --driver|--repo-root|--roadmap-dir|--phase-glob|--config)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'argus: %s needs a value\n' "$1" >&2; exit 2; }
      case "$1" in
        --driver) CLI_DRIVER="$2" ;;
        --repo-root) CLI_REPO="$2" ;;
        --roadmap-dir) CLI_ROADMAP="$2" ;;
        --phase-glob) CLI_GLOB+=("$2") ;;
        --config) CLI_CONFIG="$2" ;;
      esac
      shift 2 ;;
    --help|-h)
      echo 'usage: argus [--config FILE] [--driver phase] [--repo-root PATH] [--roadmap-dir PATH] [--phase-glob GLOB] [--dry-run|--check|--self-test|--stop]'
      echo '       argus --session NUMBER PHASE_FILE'
      echo 'A tick reads one configuration file: --config FILE, or ./config.json when it is there.'
      echo 'The file names every value the tick uses, including the pipeline and the board checkout;'
      echo 'config.sample.jsonc is the schema, and no value has a default. --self-test needs no file.'
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
SELF="$ARGUS_DIR/$(basename "$_SCRIPT_SRC")"
ARGUS="$ARGUS_DIR"
ARGUS_CONFIG_ARGS=()
ARGUS_CONFIG_VARS=(
  REPO PIPELINE ROADMAP_DIR PHASE_NUMBER_INPUT PHASE_FILE_INPUT DRIVER
  PHASE_INCLUDE PHASE_EXCLUDE WF DRIVER_STATE STALE_AFTER_SEC OUTAGE_STALE_SEC
  QUIET_MINUTES LOG_KEEP_DAYS MERGE_CHECK_MAX RECOVER_OWN_CLAIM PHASE_MACHINE_ID
  CMD_GLIDE CMD_GIT CMD_TMUX CMD_PYTHON CMD_HERMES CMD_GH SESSION_NAME
  TMUX_WIDTH TMUX_HEIGHT TMUX_IDLE_WINDOW TMUX_PHASE_WINDOW TMUX_IDLE_SCRIPT
  TMUX_REFRESH BOARD_REPO BOARD_FILE BOARD_BRANCH BOARD_REMOTE
  HERMES_PROFILE_NAME DISCORD_CHANNEL NOTIFY_DISABLED DISCORD_FALLBACK_CHANNEL
  HEARTBEAT_URL
)

clear_config_values() {
  local var
  for var in "${ARGUS_CONFIG_VARS[@]}"; do
    unset "$var" "ARGUS_SOURCE_$var"
  done
}

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
  "$PYTHON" - "$ARGUS_DIR" "$@" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import _bootstrap  # noqa: F401
from argus import config
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

CONFIG_PRECEDENCE='flag > config file'

if [ "$SELF_TEST_MODE" = 0 ]; then
  resolve_python || PYTHON="python3"
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
  REPO="$(cd "$ARGUS_DIR/.." && pwd)"
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
  *) printf 'argus: unknown driver %s; known drivers: phase\n' "$DRIVER" >&2; exit 2 ;;
esac

# The board's own branch. The self-test pins each fixture's own branch, so the
# warning below stays quiet for that invocation rather than printing per case.
PHASE_COORDINATION_BRANCH="${BOARD_BRANCH:-}"

if [ "$SELF_TEST_MODE" = 0 ]; then
  WORK_SOURCE_FLAGS=(--source roadmap)

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
  STALL="$RUN_DIR/stalled"
  CURRENT="$RUN_DIR/current"
  PIDFILE="$RUN_DIR/pid"
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
PREFLIGHT_REASON=""
roadmap_preflight() {
  PREFLIGHT_REASON=""
  # A missing private folder is not refused here. This function runs inside
  # fixtures that point the driver at a throwaway root holding only a run-state
  # folder, and refusing would make every one of them test the refusal instead of
  # the fault it exists to reproduce. The folder is required once, at startup,
  # before any of this; a machine whose folder is wrong never reaches a tick.
  if [ ! -r "$ARGUS/gitsync.py" ]; then
    PREFLIGHT_REASON="the bundled publishing tool is unreadable at $ARGUS/gitsync.py"
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


# Argus's own shell helpers, sourced from beside this file. The folder is
# computed from this script's own position rather than from the layout, because
# these two files are how the driver runs at all: a folder named in configuration
# that resolves to nothing would otherwise take the whole tick down with a
# "No such file" from bash, which names a line of this script rather than the
# setting that is wrong.
# And it is where ARGUS points from here on: the driver's own home is its own
# position, always. That is the one folder here that is not a setting, because
# the tools beside this file are how the driver selects, claims and reports —
# a machine that configured a different folder for them would be configuring
# itself not to run.
ARGUS="$ARGUS_DIR"
NOTIFIER="$ARGUS/phase_notifications.py"
for helper in phase-tmux.sh lib-stat.sh selftest-support.sh; do
  if [ ! -r "$ARGUS_DIR/$helper" ]; then
    printf 'argus: cannot read %s: this driver is broken, not misconfigured\n' \
      "$ARGUS_DIR/$helper" >&2
    exit 3
  fi
done

# Persistent tmux session: idle + phase windows. Sourced after the paths above
# are defined because the helper uses ARGUS, LOG, and REPO at call time.
# shellcheck disable=SC1091
. "$ARGUS_DIR/phase-tmux.sh"
# Portable mtime helpers (BSD stat vs GNU/uutils stat). See lib-stat.sh.
# shellcheck disable=SC1091
. "$ARGUS_DIR/lib-stat.sh"
# The self-test's throwaway checkout, and its tripwire on the machine's real
# one. Sourced here rather than defined beside the self-test because the exit
# trap outlives every function in this file, and a helper defined inside
# `driver_self_test` is gone by the time that trap runs.
# shellcheck disable=SC1091
. "$ARGUS_DIR/selftest-support.sh"

DRY_RUN=0
CUR_PHASE=""
CUR_PHASE_FILE=""
_STOPPED=0
NOTIFY_DEAD=0

ts() { date +%Y-%m-%dT%H:%M:%S%z; }

log() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s %s\n' "$(ts)" "$*"
  else
    printf '%s %s\n' "$(ts)" "$*" >>"$LOG"
  fi
}

# log, and also speak on stdout so a manual run is not silent (cron captures it).
say() { log "$*"; [ "$DRY_RUN" = 1 ] && return 0; printf '%s\n' "$*"; }

# One line out of text the selector wrote, safe to put in a log entry.
#
# The reason a selector refuses is free text from git and from the reservation
# code, and the real one is a wrapped git message. The log holds one entry per
# line, so a newline inside the reason would forge a second entry and a carriage
# return would rewrite the entry above it. Everything else printable is left
# alone, so a path or a branch name inside the reason survives for whoever reads
# it.
#
# The rendering is one way and cannot be read back. A real newline becomes a
# backslash and an n, and a reason that already held exactly those two characters
# comes out identical, so the flattened text is faithful to a reader and useless
# to a parser. That is deliberate: nothing reads it back, and the whole uncapped
# answer is written to a file beside the log line for anything that needs the
# text itself.
#
# The replacement has to be written as dollar-ansi quotes holding one backslash
# and an n. Measured on bash 3.2: dollar-ansi gives one backslash, single quotes
# around a backslash pair give two, so the log would read a doubled separator.
# Measured on bash 3.2: a bare backslash-n also gives one, but bash 5.2 and later
# rewrite an unquoted ampersand in a replacement into the text that matched, so
# the replacement stays quoted rather than relying on the version we run on.
flatten_one_line() {
  local s=${1-}
  s=${s//$'\r'/}
  s=${s//$'\n'/$'\\n'}
  s=${s//[[:cntrl:]]/ }
  printf '%s' "$s"
}

# The same one line, cut at a limit, saying how much was dropped.
#
# The limit is 500 bytes, and the number is this driver's own choice rather than
# a published one: it is picked so a single refusal stays readable in a log tail,
# and it costs nothing because the whole uncapped answer goes to a file beside
# the line. The part of this that has guidance behind it is the sanitising above,
# not the length: carriage return, line feed and delimiters are the characters a
# log reader parses.
#
# Bytes, not characters, and never half a character. The shell counts characters
# when its locale is multibyte and bytes when it is not, and cron hands this
# driver no locale at all, so a count left to the ambient one is 500 characters on
# one machine and 500 bytes on the next. The count and the cut are therefore taken
# under a function-local C locale, which makes them bytes everywhere and leaves
# the driver's own locale as it was: measured on bash 3.2, a string of two ASCII
# letters and one three-byte character counts as 7 outside this function, 9 inside
# it, and 7 again once it has returned.
#
# A byte cut lands inside a character whenever one straddles that point, and half
# a character in a log is a byte sequence the reader's terminal cannot decode. The
# cut is therefore walked back to the last whole character: trailing continuation
# bytes go, and a lead byte left without them goes too. The byte value comes from
# printf's own quoted-character form, and all 256 values were classified under a C
# locale, this machine's locale and an explicit UTF-8 one on bash 3.2: 128 ASCII,
# 64 continuation, 64 lead, none left over. The same form reads a whole character
# as its code point under a multibyte locale, which is why the slice is taken in
# the C locale rather than in whatever the driver was handed.
#
# The dropped count is in the line because a bare cut reads as a whole reason when
# it is half of one. It counts bytes, because the cut did. A cut landing between
# the backslash and the n of a flattened newline leaves one bare backslash before
# the marker; that is left alone on purpose, because the entry is read by a person
# and a backslash before a marker is legible rather than broken.
#
# Neither argument is required. This runs inside an unattended tick, so a caller
# that forgets one must cost a line of log and not the tick, which is why this
# takes a default for each where the sibling above already took one.
cap_length() {
  local s=${1-} max=${2-} LC_ALL=C cut last byte
  if (( ${#s} > max )); then
    cut="${s:0:max}"
    while [ -n "$cut" ]; do
      cap_last_byte || break
      # 128..191 is a continuation byte: 10xxxxxx, and the character it belongs to
      # starts earlier, so a cut that lands on one is always mid-character.
      (( byte >= 128 && byte <= 191 )) || break
      cut="${cut%?}"
    done
    if [ -n "$cut" ]; then
      # 192..255 is a lead byte: 11xxxxxx. One of those is the first byte of a
      # character whose remaining bytes were cut away, so it goes too.
      cap_last_byte && (( byte >= 192 )) && cut="${cut%?}"
    fi
    printf -v s '%s...[+%d bytes truncated]' "$cut" "$(( ${#s} - ${#cut} ))"
  fi
  printf '%s' "$s"
}

# Read the last byte of `cut` into `byte`, as a value from 0 to 255, and return 0.
#
# The sign is the whole difficulty, and it changed in bash 4. `printf '%d' "'X"`
# reports a byte with the high bit set as a NEGATIVE number on bash 3.2 and as
# the UNSIGNED value on bash 4 and later: measured here, the first byte of a
# three-byte character comes back as -28 on bash 3.2.57 and as 228 on bash 5.3.20,
# from the same script and the same locale. Testing the sign therefore passes on
# one version and silently does nothing on the other, which is how this function
# came to emit half a character on every machine running a current bash while
# passing on the machine that reported it.
#
# So the value is normalised to 0..255 here and every caller tests unsigned
# thresholds. `cut` must be the variable name: the caller sets it.
cap_last_byte() {
  last="${cut: -1}"
  printf -v byte '%d' "'$last" || return 1
  (( byte < 0 )) && byte=$(( byte + 256 ))
  return 0
}

# One line per checkout saying where it actually is, read from the branch
# receipt by the action each checkout reported.
#
# The receipt's top-level "branch" is the name that was asked for, and it is
# the same string for both checkouts. Printing it described a phase branch for
# whichever checkout was really still on master, which is how a swapped pair
# read as a correct launch in the log. Each line below is taken from the
# checkout's own entry instead, and the public one is named by its path rather
# than by position, so the line cannot quietly swap the two.
branch_state_line() {
  "$PYTHON" - "$1" "${BOARD_REPO:-}" <<'PY'
import json, sys
from pathlib import Path
try:
    with open(sys.argv[1]) as fh:
        out = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"branch state unreadable ({exc})")
    raise SystemExit(0)
for repo in out.get("repos", []):
    path = repo.get("repo", "")
    role = "private" if Path(path).resolve() == Path(sys.argv[2]).resolve() else "public"
    where = repo.get("after_branch") or repo.get("action", "?")
    print(f"{role} {where}", end="; ")
PY
}

# One line saying how far the integration branch got into the phase branch.
#
# A conflict is reported with the paths, not as a bare failure, because the
# phase agent is the one who resolves it and a log line naming the files is the
# difference between a phase that finishes and one that stops to ask.
resync_state_line() {
  "$PYTHON" - "$1" "${BOARD_REPO:-}" <<'PY'
import json, sys
from pathlib import Path
try:
    with open(sys.argv[1]) as fh:
        out = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"resync state unreadable ({exc})")
    raise SystemExit(0)
for repo in out.get("repos", []):
    path = repo.get("repo", "")
    role = "private" if Path(path).resolve() == Path(sys.argv[2]).resolve() else "public"
    action = repo.get("action", "?")
    if repo.get("conflicted"):
        print(f"{role} {action} on "
              f"{', '.join(repo['conflicted'])}", end="; ")
    elif repo.get("ok"):
        print(f"{role} {action}", end="; ")
    else:
        blockers = "; ".join(repo.get("blockers") or ["?"])
        print(f"{role} {action} ({blockers})", end="; ")
PY
}

# Best-effort send: bounded by a timeout (when available) and with stdin
# closed, so a hung or interactive hermes can never stall the run.
send_one() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 20 "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout 20 "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  else
    "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  fi
}

# Notifications are strictly best-effort: a missing profile, a dead Discord or a
# hung hermes must never block or halt the pipeline. On total failure the channel
# is marked dead for this process, so we stop retrying and stop spamming.
notify() {
  local msg="$1" target="discord:$CHANNEL"
  if [ "$NOTIFY_DEAD" = 1 ]; then
    log "notify skipped (channel unavailable): $msg"
    return 0
  fi
  if [ "${NOTIFY_DISABLED:-0}" = 1 ]; then
    log "notify (disabled): $msg"
    return 0
  fi
  if [ -n "${DISABLE_NOTIFY:-}" ] || [ -n "${DISABLE_DISCORD:-}" ]; then
    log "notify (disabled): $msg"
    return 0
  fi
  if [ -z "$PROFILE" ] && [ -z "$CHANNEL" ]; then
    log "notify skipped (not configured): $msg"
    return 0
  fi
  if ! command -v "$CMD_HERMES" >/dev/null 2>&1 && [ ! -x "$CMD_HERMES" ]; then
    log "notify skipped (hermes unavailable: $CMD_HERMES): $msg"
    return 0
  fi
  if send_one "$target" "$msg"; then
    log "notify ok: $msg"; rm -f "$NOTIFY_BROKEN"; return 0
  fi
  sleep 2
  if send_one "$target" "$msg"; then
    log "notify ok (retry): $msg"; rm -f "$NOTIFY_BROKEN"; return 0
  fi
  if [ -n "$FALLBACK" ] && send_one "discord:$FALLBACK" "$msg"; then
    log "notify via fallback channel: $msg"; return 0
  fi
  printf 'notify broken at %s: %s\n' "$(ts)" "$msg" >"$NOTIFY_BROKEN"
  NOTIFY_DEAD=1
  log "notify FAILED (wrote $NOTIFY_BROKEN); further sends skipped this run: $msg"
}

notify_phase() {
  local phase_file="$1" event="$2" reason="${3:-}" exit_code="${4:-}"
  local quiet_minutes="${5:-$QUIET_MINUTES}" message phase_number
  local args=("${ARGUS_CONFIG_ARGS[@]}" --phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")
  [ -n "$reason" ] && args+=(--reason "$reason")
  [ -n "$exit_code" ] && args+=(--exit-code "$exit_code")
  message="$("$PYTHON" "$NOTIFIER" "${args[@]}" 2>/dev/null || true)"
  if [ -n "$message" ]; then
    notify "$message"
    return 0
  fi
  phase_number="$("$PYTHON" - "$ARGUS" "$phase_file" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[3:])
from argus.conventions import phase_file_parts
parts = phase_file_parts(sys.argv[2])
print(parts[0] if parts else "unknown")
PY
  )"
  notify "Phase ${phase_number:-unknown} needs attention."
}

# Say once that the coordination board is unreachable, not once every tick.
#
# The cron entry lives in the Makefile as CRON_LINE, and it is every ten minutes
# across sixteen hours of the day, so an unconditional send would be about
# ninety-six identical messages a day and the operator would learn to ignore the
# channel. That figure is derived from the Makefile rather than read from any
# installed crontab, because no crontab on any machine was checked.
#
# Creating the marker directory is the compare-and-swap: of any number of ticks
# that see the outage together, exactly one creates it and sends. The caller
# removes the marker on the first tick the board answers, which re-arms the next
# outage to speak for itself, and a send that failed removes it here, so a later
# tick tries again rather than staying silent for ever about an outage nobody was
# told of.
#
# A marker left behind by an outage nobody watched end is a different thing, and
# it would silence the channel from then on, because the only thing that clears a
# marker is a tick that reads a usable selection. The marker therefore also
# carries, beside it rather than inside it, the time the outage was first seen,
# and a marker older than the limit below is treated as belonging to an outage
# that is no longer plausibly the current one: it is removed and this tick takes
# its place and speaks. The limit is nine hours because the schedule leaves eight
# hours and ten minutes with no tick at all, so anything shorter would send a
# second message about a single outage that simply ran overnight.
#
# Nothing may ever be written inside the marker directory. It is removed with
# rmdir, which refuses a directory holding anything, so one stray file inside it
# would make every removal fail and every later outage silent with no error
# anywhere. The first-seen time is a separate file for that reason and not a file
# within it.
notify_coordination_outage() {
  local reason="${1-}" marker="$RUN_DIR/coordination-outage"
  local seen="$RUN_DIR/coordination-outage-first-seen"
  local stale_after="$OUTAGE_STALE_SEC" recorded now age
  if [ -d "$marker" ]; then
    recorded="$(cat "$seen" 2>/dev/null || true)"
    case "$recorded" in
      ''|*[!0-9]*) recorded=0 ;;
    esac
    now="$(date +%s 2>/dev/null || echo 0)"
    age=$((now - recorded))
    if [ "$recorded" -gt 0 ] && [ "$age" -gt "$stale_after" ]; then
      rmdir "$marker" 2>/dev/null || true
      rm -f "$seen"
      log "outage marker was $((age / 3600))h old with no sign of recovery; treating it as a new outage"
    fi
  fi
  if mkdir "$marker" 2>/dev/null; then
    date +%s >"$seen" 2>/dev/null || true
    notify "The coordination board cannot be reached, so no phase can start.

The selector said: $reason

Nothing was launched and nothing can be until this is fixed. The selector's whole answer is in $RUN_DIR/coordination-selection.json"
    # notify records a total send failure in its own marker and gives up for the
    # rest of the run, so this is the only place a failed send is visible here.
    if [ "$NOTIFY_DEAD" = 1 ]; then
      rmdir "$marker" 2>/dev/null || true
      rm -f "$seen"
    fi
  fi
  return 0
}

phase_run_dir() {
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "${2:-}" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[5:])
from argus.conventions import phase_run_dir
print(phase_run_dir(sys.argv[2], int(sys.argv[3]), sys.argv[4] or None))
PY
}

phase_state() {
  local file
  file="$(phase_run_dir "$1" "${2:-}")/run.json"
  "$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state",""))' \
    "$file" 2>/dev/null || true
}

# The state a prior run ended in when glide would refuse to rerun it without
# --fresh. glide drops a `blocked` marker and resumes; every other marker it
# refuses, including one that does not parse or carries no state, so those ask
# for a fresh start too. Only an absent marker returns empty, because there is
# nothing to archive.
terminal_state_of_file() {
  local file="$1" state
  [ -f "$file" ] || return 0
  state="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state",""))' \
    "$file" 2>/dev/null || true)"
  case "$state" in
    blocked) return 0 ;;
    "") printf '%s' "unreadable" ;;
    *) printf '%s' "$state" ;;
  esac
}

# A signal-driven stop must halt and notify: bash dies before any trailing
# code after a signal, so the work is done in a trap, marker first. (Ctrl-C
# reaches this; tmux kill-session does NOT signal the pane, which is why the
# driver also records the session PID and treats a live PID with no session
# as an orphan.)
on_stop() {
  local rc="$1"
  [ "$_STOPPED" = 1 ] && exit "$rc"
  _STOPPED=1
  if [ -n "$CUR_PHASE" ]; then
    printf 'phase %s stopped by signal (rc=%s) at %s\n' \
      "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    notify_phase "$CUR_PHASE_FILE" stopped-signal
  fi
  rm -f "$CURRENT" "$PIDFILE"
  exit "$rc"
}

# Commit and push one finished phase's closing private run state.
#
# The runner writes the ledger's finalize row, the run marker, the usage files
# and the deleted resume record *after* finalize's publish commit, so those
# writes were never carried. The next phase's publish then found them dirty and
# refused with "working tree is dirty after phase cleanup", which halted the
# line. This is the private-only, phase-scoped commit that lands them. A failure
# is logged and never halts: the next phase's publish reports it if it recurs.
gitsync_clean() {
  local number="$1" rc=0
  "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    --phase "$number" >"$RUN_DIR/phase-$number-run-state.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not commit phase $number closing run state (rc=$rc; see $RUN_DIR/phase-$number-run-state.json)"
  fi
  return 0
}

# Land every phase's leftover closing run state before this launch.
#
# gitsync_clean lands a phase's closing writes when its run ends cleanly, but a
# run that ended without one — interrupted, blocked, or finished before that
# cleanup existed — leaves the writes dirty. The next phase's publish then
# refuses with "working tree is dirty after phase cleanup" and halts the line.
# Committing every phase's leftovers here, each scoped to its own directory,
# closes that gap before the launch without carrying another phase's partial
# records into this phase's commit. A failure is logged and never halts: the
# publish that would have refused reports it instead.
gitsync_land_leftovers() {
  local rc=0
  "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    >"$RUN_DIR/run-state-leftovers.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not land leftover run state before launch (rc=$rc; see $RUN_DIR/run-state-leftovers.json)"
  fi
  return 0
}

# Record an exhausted remedy loop and continue the line.
#
# Exhausting the approver's rounds is a fact about the budget, not a verdict on
# the work, so the run ends non-terminal (`remedy_escalated`): this writes the
# open findings and where their grounds live to the run directory, releases the
# phase's claim on the board, and writes no halt marker. A failure in either
# half is logged; the run record's own state is what the selector reads, so the
# phase is skipped either way.
handle_escalation() {
  local number="$1" rel="$2" phase_dir out rc=0
  phase_dir="$WF/phase-$(printf '%06d' "$number")"
  out="$RUN_DIR/escalation-$number.json"
  local args=("${ARGUS_CONFIG_ARGS[@]}" --run-dir "$phase_dir" --phase "$number" --repo-root "$REPO")
  [ -z "${PHASE_MACHINE_ID:-}" ] || args+=(--machine-id "$PHASE_MACHINE_ID")
  [ -z "${CLIO_RESERVATION_ID:-}" ] || args+=(--reservation-id "$CLIO_RESERVATION_ID")
  [ -z "${CLIO_RESERVATION_GENERATION:-}" ] || args+=(--generation "$CLIO_RESERVATION_GENERATION")
  "$PYTHON" "$ARGUS/remedy_escalation.py" "${args[@]}" \
    >"$out" 2>/dev/null || { rc=$?; log "escalation record for phase $number could not be completed (rc=$rc; see $out)"; }
  # Land the record and release the claim before saying so, so a person reading
  # the notification finds the evidence where it points.
  gitsync_clean "$number"
  tmux_set_title "phase $number escalated"
  notify_phase "$rel" escalated
  return 0
}

run_session() {
  local number="$1" rel="$2" rc=0 state
  CUR_PHASE="$number"
  CUR_PHASE_FILE="$rel"
  mkdir -p "$RUN_DIR"
  trap 'on_stop 129' HUP
  trap 'on_stop 130' INT
  trap 'on_stop 143' TERM
  printf '%s\n' "$$" >"$PIDFILE"
  cd "$REPO"
  tmux_set_title "phase $number running"
  notify_phase "$rel" started
  if [ -n "${DRIVER_STUB_RC:-}" ]; then
    log "STUB mode: simulating glide (rc=$DRIVER_STUB_RC)"
    # A stub can be told to wait for a file instead of sleeping a fixed span. A
    # fixed sleep is a duration the test guesses: on a loaded host the guess is
    # over before the condition under test is set up, and a check that needs the
    # stub to still be running then fails because the stub finished. Waiting for a
    # file the test creates at the moment it needs the stub alive removes the
    # guess. The timeout bounds the wait so a forgotten release cannot hang the
    # self-test.
    if [ -n "${DRIVER_STUB_WAIT_FILE:-}" ]; then
      # HUP is ignored while the stub waits, because the orphan case depends on
      # the pane process outliving the kill of its window. Left alone, the trap
      # would fire on the kill, halt the line, and remove the recorded PID — the
      # driver would then correctly find nothing to orphan and the case would
      # never be reached. Ignoring it here is what makes this a test of the
      # orphan branch rather than of the signal branch.
      trap '' HUP
      # Announced before waiting, not after: the recorded PID appears the moment
      # the pane starts, which is before the stub is in a state to be orphaned. A
      # test that acted on the PID alone could kill the window while the trap was
      # still the one that halts, and would then be testing the signal branch
      # while believing it tested the orphan branch.
      [ -z "${DRIVER_STUB_READY_FILE:-}" ] || : >"$DRIVER_STUB_READY_FILE"
      local waited=0
      while [ ! -e "$DRIVER_STUB_WAIT_FILE" ] && [ "$waited" -lt 600 ]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      [ -e "$DRIVER_STUB_WAIT_FILE" ] \
        || log "STUB mode: timed out after $((waited * 100))ms waiting for $DRIVER_STUB_WAIT_FILE"
    else
      sleep "${DRIVER_STUB_SLEEP:-2}"
    fi
    rc="$DRIVER_STUB_RC"
  else
    local glide_inputs=() input input_values
    if input_values="$("$PYTHON" - "$ARGUS" "$REPO" "$number" "$rel" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[5:])
from argus.conventions import pipeline_inputs
inputs = pipeline_inputs(sys.argv[2], int(sys.argv[3]), sys.argv[4])
for key in (conventions.setting("number-input", "phase_number"), conventions.setting("file-input", "phase_file")):
    value = inputs[key]
    print(f"{key}={value if isinstance(value, str) else json.dumps(value)}")
PY
    )"; then
      while IFS= read -r input; do glide_inputs+=(--input "$input"); done <<<"$input_values"
      # glide refuses to rerun a run that already reached a terminal state unless
      # --fresh archives that run first. The selector skips the states it knows
      # need a decision, but any other terminal run it hands back (an older end
      # state, or a state added later) would wedge every tick on that refusal:
      # the driver would launch, glide would exit 2, and the driver would halt.
      # Archive and restart instead. A `blocked` run is resumable and is left
      # alone, and a missing run.json means there is nothing to archive.
      local prior_state fresh_args=()
      prior_state="$(terminal_state_of_file "$(phase_run_dir "$number" "$rel")/run.json")"
      if [ -n "$prior_state" ]; then
        fresh_args+=(--fresh)
        log "phase $number run is terminal ($prior_state); restarting it with --fresh"
      fi
      "$CMD_GLIDE" run --pipeline "$PIPELINE" \
        "${fresh_args[@]+"${fresh_args[@]}"}" "${glide_inputs[@]}" || rc=$?
    else
      log "invalid pipeline inputs; glide was not launched"
      rc=2
    fi
  fi
  state="$(phase_state "$number" "$rel")"
  if [ "$state" = "remedy_escalated" ]; then
    # The remedy approver used every round and findings are still open. That is
    # a budget fact, not a verdict: record the residue, release the claim, and
    # continue with the next phase. No halt marker is written, and the selector
    # skips a remedy_escalated phase, so the line neither stops nor loops on it.
    handle_escalation "$number" "$rel"
    rm -f "$CURRENT" "$PIDFILE"
    exit 0
  fi
  case "$rc" in
    0)   # Land the closing run-state writes before the next phase's publish
         # has to look at them; see gitsync_clean.
         gitsync_clean "$number"
         tmux_set_title "phase $number done"
         notify_phase "$rel" completed ;;
    3)   : ;;
    130) tmux_set_title "HALTED phase $number (interrupted)"
         notify_phase "$rel" interrupted "the process received an interrupt signal" ;;
    *)   printf 'phase %s rc=%s state=%s at %s\n' \
           "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
          # The title is set before the window closes, so the halt survives the
          # phase window going away and the idle display taking over.
          tmux_set_title "HALTED phase $number (rc=$rc)"
          notify_phase "$rel" stopped "" "$rc" ;;
  esac
  rm -f "$CURRENT" "$PIDFILE"
  exit "$rc"
}

# Busy means "a phase window is running", not "the session exists". The session
# is persistent and always exists once created, so testing it would block the
# line forever. A dead-but-retained phase window is reclaimed, never the
# session, so a display attached to the idle window is not disturbed.
tmux_busy() {
  tmux_phase_window_alive && return 0
  if tmux_window_exists; then
    log "stale dead phase window in '$SESSION'; reclaiming it"
    tmux_phase_window_close
  fi
  return 1
}


# While a session is live, alert once if its output has gone quiet too long.
#
# The clock deliberately does NOT watch the session log. The engine prints its
# recovery line there every 5 minutes for as long as a step is quiet, so a session
# log can never look stale while a step is stuck: the watchdog would be reset by
# the very message that means "still stuck". The phase directory is the signal
# instead — run logs, the console capture, and the `activity` marker the engine
# touches whenever the harness produces output. `reminders.log` is excluded
# because the reminder audit is engine chatter too, and it fires a few times per
# step at most.
check_stall() {
  [ -f "$STALL" ] && return 0
  local n phase_dir newest now age phase_file
  n="$(cat "$CURRENT" 2>/dev/null || true)"
  [ -n "$n" ] || return 0
  phase_dir="$(phase_run_dir "$n")"
  newest="$(phase_activity_mtime "$phase_dir")"
  [ "$newest" -gt 0 ] || return 0
  now="$(date +%s)"; age=$((now - newest))
  if [ "$age" -gt "$STALE_AFTER_SEC" ]; then
    printf 'stalled: phase %s no agent output for %ss at %s\n' \
      "$n" "$age" "$(ts)" >"$STALL"
    phase_file="$("$PYTHON" - "$ARGUS" "$REPO" "$n" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.conventions import resolve_phase_file
print(resolve_phase_file(sys.argv[2], int(sys.argv[3])) or "")
PY
    )"
    if [ -n "$phase_file" ]; then
      notify_phase "$phase_file" stalled "" "" "$((STALE_AFTER_SEC / 60))"
    else
      notify "Phase $n looks stalled.

There has been no agent output for $STALE_AFTER_SEC seconds. The session is still running; inspect it before stopping the work."
    fi
  fi
}

# The newest mtime in the phase directory, ignoring the engine's own sidecars.
phase_activity_mtime() {
  local dir="$1" file newest
  [ -d "$dir" ] || { printf '0'; return 0; }
  newest=0
  for file in "$dir"/*; do
    [ -f "$file" ] || continue
    case "$(basename "$file")" in
      reminders.log) continue ;;
    esac
    stat_epoch "$file"
    printf '\n'
  done | sort -rn | head -1 | { read -r top || top=''; printf '%s' "${top:-0}"; }
}

prune() {
  [ "$DRY_RUN" = 1 ] && return 0
  find "$RUN_DIR" -maxdepth 1 -name 'session-*.log' -mtime "+$LOG_KEEP_DAYS" \
    -delete 2>/dev/null || true
}

heartbeat() {
  local url="${HEARTBEAT_URL:-}"
  [ -n "$url" ] || return 0
  curl -fsS -m 10 --retry 3 "$url" >/dev/null 2>&1 \
    || log "heartbeat ping failed ($url)"
}

# Run the work selector and return its stdout, with its stderr recorded.
#
# stderr is captured because that is where a refusal names its reason and
# nowhere else does: an unknown source or an unreadable roadmap folder gives
# empty stdout and exit 2, so without this the log would say only that nothing
# was selected and the operator would have to reproduce it by hand.
#
# It is captured and passed through, not moved. The file is the raw reason for a
# human to read; driver.log gets it under a prefix so a line the driver wrote is
# distinguishable from one the selector wrote; and stderr still carries the raw
# text, which is exactly what reached the cron log before this was captured at
# all. Passing it through is therefore additive, and an unconfigured machine's
# log surface is unchanged rather than reshaped.
selector_run() {
  local err="$RUN_DIR/selection.err" out rc=0
  mkdir -p "$RUN_DIR"
  out="$("$PYTHON" "$ARGUS/next_phase.py" "${ARGUS_CONFIG_ARGS[@]}" \
    "${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}" "$@" 2>"$err")" || rc=$?
  if [ -s "$err" ]; then
    # Not log() on the driver.log side: on a dry run log() prints to stdout, and
    # this function's stdout is the captured result, so the reason would land
    # inside it. A dry run keeps the raw text on stderr and records nothing,
    # which is what it did before.
    if [ "$DRY_RUN" != 1 ]; then
      sed 's/^/selector: /' "$err" >>"$LOG"
    fi
    cat "$err" >&2
  fi
  printf '%s' "$out"
  return "$rc"
}

coordination_select() {
  # The JSON result is deliberately consumed instead of parsing the legacy
  # tab output. A failed fetch/push must never turn into an unclaimed launch.
  selector_run --server --machine-id "$PHASE_MACHINE_ID" --json "$@"
}

# One field out of the selector's JSON result, or nothing when there is no result.
#
# "Or nothing" is the case that matters. Every refusal comes back as an empty
# result with a non-zero exit, because an unreadable folder and a queue that has
# run dry are indistinguishable from the exit code alone. Parsing that empty
# result therefore has to answer with nothing rather than a traceback: a
# traceback is Python's error, printed where the operator is reading, on the
# exact path whose job is to name what went wrong. The caller already treats an
# empty field as "no value" and refuses the launch.
selection_value() {
  local key="$1"
  "$PYTHON" -c 'import json, sys
try:
    print(json.load(sys.stdin).get(sys.argv[1], ""))
except ValueError:
    pass
' "$key"
}

# One line per phase the selector skipped because its pull request is open.
#
# The selector no longer stops on an open pull request, so nothing else runs
# the merge check, and a receipt would read awaiting_review for ever: the phase
# that was merged never gets recorded as merged and the board keeps no
# completion evidence for it. This is the only place that happens, which is why
# it runs before the selection is acted on rather than after.
#
# Newest first, deliberately. The phase that blocks the line is the highest
# numbered one still waiting, because the next phase is stacked on the highest
# open predecessor. Checking in phase order spends the cap on old receipts and
# can leave the blocking phase unexamined on every tick, which is the stall
# this whole path exists to end.
pending_review_lines() {
  "$PYTHON" -c '
import json, sys
entries = json.load(sys.stdin).get("awaiting_review", []) or []
for entry in sorted(entries, key=lambda e: str(e.get("phase") or ""), reverse=True):
    print("\t".join([str(entry.get("phase") or ""),
                     str(entry.get("branch") or ""),
                     str(entry.get("pull_request") or "")]))
'
}

# Say which phases the selector passed over because their run ended owing a
# person a decision: a rejected run, or a remedy loop that ran out of rounds.
# The selector skips them so the line can advance; this is what stops the skip
# being silent. Reported on every selection, including one that launched
# nothing, so a queue waiting on a decision reads as waiting rather than empty.
report_needs_decision() {
  local selection="$1" phase state path
  while IFS=$'\t' read -r phase state path; do
    [ -n "$phase" ] || continue
    log "phase $phase ended $state; skipped until a person decides ($path)"
  done < <(printf '%s' "$selection" | "$PYTHON" -c '
import json, sys
entries = json.load(sys.stdin).get("needs_decision", []) or []
for entry in entries:
    print("\t".join([str(entry.get("phase") or ""),
                     str(entry.get("state") or ""),
                     str(entry.get("path") or "")]))
' 2>/dev/null)
}

# Return a checkout sitting on a published phase branch to the integration
# branch, before anything that would refuse on the dead branch.
#
# The same `--mode tidy` the launch path runs, called here as well as there. It
# is idempotent and needs no network: it reads a receipt off disk and asks git
# for a clean tree and a local integration branch. Running it before the
# checkout sync is what makes a merged-then-deleted phase branch recoverable —
# see the ordering note in _drive().
#
# A refusal is a refusal, with one deferral. The phase on this branch is not
# published yet, the tree is dirty, or the integration branch is missing: each
# of those is tidy's own fault to report, and the tick stops. But a root that
# is not a git working tree is not tidy's business at all — the sync gate two
# lines below reports it with its own message, and stopping here would replace
# that message with one naming the wrong step. So that one result is deferred
# and the gate answers it.
tidy_published_checkout() {
  local rc=0 not_a_tree=0
  "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode tidy \
    >"$RUN_DIR/phase-tidy.json" 2>/dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  not_a_tree="$("$PYTHON" - "$RUN_DIR/phase-tidy.json" <<'PROBE' 2>/dev/null || printf 'no'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError):
    print("no")
    raise SystemExit(0)
repos = doc.get("repos") or []
# Every checkout refused, and each refused for that one reason. One checkout
# refusing for it while another refused for something else is not this case.
deferred = repos and all(
    any(b == "not a git working tree" for b in (r.get("blockers") or []))
    for r in repos)
print("yes" if deferred else "no")
PROBE
)"
  if [ "$not_a_tree" = "yes" ]; then
    return 0
  fi
  log "could not return a published phase's checkout to the integration branch; refusing to launch (see $RUN_DIR/phase-tidy.json)"
  return 1
}

# Run the merge check for every phase waiting on a pull request. Sets
# PENDING_PUBLISHED to the number that became published, so the caller knows
# whether the selection it just made has gone stale and must be recomputed.
#
# A global rather than an echoed count, because log writes to stdout in a dry
# run and to the log file otherwise, so anything captured from this function's
# stdout would carry log lines on one path and not the other.
#
# The exit code alone is deliberately not what is read. `--mode published` exits
# non-zero both when the merge has not happened and when the merge HAS happened
# but the checkout could not be moved afterwards; treating the second as the
# first reported a finished phase as still waiting. The result JSON says which,
# so that is what is read.
PENDING_PUBLISHED=0
MERGE_CHECK_HEALTH="ok"
confirm_pending_reviews() {
  local phase branch pr rc=0 state checked=0 cap
  PENDING_PUBLISHED=0
  MERGE_CHECK_HEALTH="ok"
  # Bounded, because each phase costs a gh call and possibly a fetch, and a
  # selection is not a place where unbounded network work belongs. Five keeps
  # the worst case (five calls at the 15s bound in gitsync.py) to about 75
  # seconds, inside the cron interval. Any stack deeper than that is checked on
  # a later tick, and hitting the cap says so.
  cap="$MERGE_CHECK_MAX"
  while IFS="$(printf '\t')" read -r phase branch pr; do
    [ -n "$phase" ] || continue
    if [ "$checked" -ge "$cap" ]; then
      log "merge check: stopped after $cap phase(s); the rest are checked on a later tick"
      MERGE_CHECK_HEALTH="capped at $cap"
      break
    fi
    if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
      "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
        --mode published --phase "$phase" \
        >"$RUN_DIR/phase-published-$phase.json" 2>/dev/null || rc=$?
    elif [ "${DRIVER_STUB_MERGED_PHASE:-}" = "$phase" ]; then
      # The stub keeps the self-test off the network and off real repositories.
      rc=0
      printf '{"state": "PUBLISHED"}\n' >"$RUN_DIR/phase-published-$phase.json"
    elif [ "${DRIVER_STUB_COORDINATION:-}" = "review" ]; then
      rc=1
      printf '{"state": "BLOCKED", "repos": [{"ok": false, "github": {"state": "unknown", "detail": "gh exited 4"}}]}\n' \
        >"$RUN_DIR/phase-published-$phase.json"
    else
      rc=1
    fi
    checked=$((checked + 1))
    state="$(printf '%s' "$(selection_value state <"$RUN_DIR/phase-published-$phase.json")" 2>/dev/null || true)"
    case "$state" in
      PUBLISHED)
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        log "phase $phase is merged; marked published and returned the public checkout to the integration branch"
        ;;
      PUBLISHED-TIDY-PENDING)
        # The merge is real and recorded; only the branch switch failed. The
        # tidy step below finishes it, so this is progress, not a wait.
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        log "phase $phase is merged and recorded, but the checkout could not be returned to the integration branch yet; the tidy step will retry it"
        ;;
      *)
        if grep -q '"state": *"unknown"' "$RUN_DIR/phase-published-$phase.json" 2>/dev/null; then
          MERGE_CHECK_HEALTH="UNAVAILABLE (gh could not be asked)"
        fi
        log "phase $phase still waits: $(sed -n 's/.*"blockers": \[\(.*\)\].*/\1/p' "$RUN_DIR/phase-published-$phase.json" 2>/dev/null | head -1)"
        log "  branch: $branch"
        [ -z "$pr" ] || log "  pull request: $pr"
        if [ "$MERGE_CHECK_HEALTH" = "UNAVAILABLE (gh could not be asked)" ]; then
          log "  the squash-merge check is DEGRADED: a squash-merged phase will keep waiting until gh works again"
        fi
        ;;
    esac
    rc=0
  done < <(pending_review_lines <<<"$1")
  log "merge check: $MERGE_CHECK_HEALTH ($checked phase(s) examined)"
  # A degraded merge check is announced, not just logged. This is the condition
  # that hid the stall: gh could not be asked, every phase stayed "awaiting
  # review", the next phase was cut from a merged branch, and the branch guard
  # refused it on every tick with nothing but a log line to say so. The operator
  # has to hear it from the line, because the symptom they see is a phase that
  # never starts and a reason that does not explain it.
  if [ "$MERGE_CHECK_HEALTH" = "UNAVAILABLE (gh could not be asked)" ]; then
    notify "The phase line cannot confirm merges right now.

GitHub could not be asked, so a pull request a human already squash-merged may still read as waiting. A phase whose branch was deleted after merging cannot be proved merged from local git alone, and the line will refuse to start the next phase until gh works.

Fix: authenticate gh on this host, then let the driver rerun."
  fi
}

_drive() {
  cd "$REPO"

  if [ "$DRY_RUN" = 1 ]; then
    [ -f "$HALT" ] && { log "dry-run: halted; would refuse to advance"; return 0; }
    tmux_phase_window_alive 2>/dev/null \
      && { log "dry-run: phase window is running; would skip"; return 0; }
    local selection selection_rc=0 state unknown_state
    if ! roadmap_preflight; then
      log "dry-run: $PREFLIGHT_REASON; no launch"
      return 0
    fi
    selection="$(coordination_select --read-only)" || selection_rc=$?
    if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 1 ] \
        && [ "$selection_rc" -ne 2 ] && [ "$selection_rc" -ne 3 ]; then
      log "dry-run: coordination unavailable; refusing to launch"
      return 0
    fi
    state="$(printf '%s' "$selection" | selection_value state)"
    report_needs_decision "$selection"
    case "$state" in
      AVAILABLE)
        local number rel
        number="$(printf '%s' "$selection" | selection_value phase)"
        rel="$(printf '%s' "$selection" | selection_value path)"
        log "dry-run: would reserve and launch phase $number in tmux '$SESSION'"
        printf 'would launch: tmux new-window -d -t %s -n phase -c %s "bash %s --session %s %s"\n' \
          "$SESSION" "$REPO" "$SELF" "$number" "$rel"
        ;;
      WAIT_FOR_CLAIM)
        wait_phase="$(printf '%s' "$selection" | selection_value phase)"
        wait_owner="$(printf '%s' "$selection" | selection_value owner)"
        if [ "$RECOVER_OWN_CLAIM" = 1 ] \
            && [ "$wait_owner" = "$PHASE_MACHINE_ID" ] \
            && [ "$wait_phase" != "" ]; then
          log "dry-run: would recover this host's own claim on phase $wait_phase and launch it"
        else
          log "dry-run: WAIT_FOR_CLAIM (phase $wait_phase, owner $wait_owner); no launch"
        fi
        ;;
      COORDINATION_INCONSISTENT)
        log "dry-run: coordination state conflicts with completion evidence; no launch"
        ;;
      COORDINATION_UNAVAILABLE)
        # Fixed on its own terms rather than shared with the tick below: this
        # branch dispatches on a different variable and answers 0 because a dry
        # run reports rather than refuses. What it must not do is drop the
        # reason, which is the whole defect. The run directory is made first
        # because the dry-run path returns before the tick creates it, and a
        # missing directory would fail the redirect and leave the log line
        # naming a file that was never written.
        #
        # The answer goes to its own file, not to the one the tick writes. A dry
        # run uses the real run directory unless it has been given another, and an
        # operator who runs one during an outage is asking the same question the
        # tick asked, so writing over the tick's answer would replace the evidence
        # the outage notification points at with a different answer to it.
        mkdir -p "$RUN_DIR"
        printf '%s\n' "$selection" >"$RUN_DIR/coordination-selection.dry-run.json"
        unavailable_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unavailable_error" ]; then
          unavailable_error="$(cap_length "$(flatten_one_line "$unavailable_error")" 500)"
          log "dry-run: COORDINATION_UNAVAILABLE ($unavailable_error); no launch (see $RUN_DIR/coordination-selection.dry-run.json)"
        else
          log "dry-run: COORDINATION_UNAVAILABLE; no launch (see $RUN_DIR/coordination-selection.dry-run.json)"
        fi
        ;;
      BLOCKED_ON_GATE)
        gate_phase="$(printf '%s' "$selection" | selection_value phase)"
        gate_error="$(printf '%s' "$selection" | selection_value error)"
        log "dry-run: BLOCKED_ON_GATE (phase $gate_phase: $gate_error); no launch"
        ;;
      NO_PHASE)
        log "dry-run: no uncompleted phase"
        ;;
      *)
        # A state nobody anticipated is the case where a reason matters most, so
        # the error field is named when the selector sent one. The state name
        # stays in the line either way, so an empty field reads as it did before.
        # The state name goes through the same two helpers as the reason, because
        # the selector's answer is free text in that field too: a newline in it
        # would forge a second entry exactly as one in the reason would.
        unknown_state="$(cap_length "$(flatten_one_line "$state")" 500)"
        unknown_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unknown_error" ]; then
          unknown_error="$(cap_length "$(flatten_one_line "$unknown_error")" 500)"
          log "dry-run: unexpected coordination result '$unknown_state' ($unknown_error); no launch"
        else
          log "dry-run: unexpected coordination result '$unknown_state'; no launch"
        fi
        ;;
    esac
    return 0
  fi

  mkdir -p "$RUN_DIR"

  if [ -f "$HALT" ]; then
    log "halted ($(tr '\n' ' ' <"$HALT")); refusing to advance"
    return 0
  fi

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    flock -n 9 || { log "another driver instance holds the lock; skip"; return 0; }
  fi

  if tmux_busy; then
    check_stall
    log "phase window is running; skip"
    return 0
  fi

  # No phase window, but a recorded live PID means an orphaned run (e.g. after
  # `tmux kill-window`, which does not signal the pane). The session itself
  # outlives every phase, so it proves nothing. Halt rather than start a
  # second, concurrent run.
  if [ -f "$PIDFILE" ]; then
    local opid
    opid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$opid" ] && kill -0 "$opid" 2>/dev/null; then
      log "orphaned run detected (pid $opid, no phase window); halting"
      [ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
      notify "a phase run (pid $opid) is alive but its tmux phase window is gone (orphaned). Line stopped; investigate."
      return 0
    fi
    rm -f "$PIDFILE"
  fi

  # No live phase: clear per-session state a hard kill may have left behind.
  rm -f "$CURRENT" "$STALL"
  prune
  # Keep the display truthful between phases. A halted line says so here too, so
  # the reason is visible even if the titlebar is not rendered.
  if [ -f "$HALT" ]; then
    tmux_set_title "HALTED - $(tr '\n' ' ' <"$HALT" | cut -c1-60)"
  else
    tmux_set_title "idle - waiting for next phase"
  fi

  # The fence is empty until a path fills it in. Every branch that reaches the
  # launch must set both, because `set -u` makes a missing one fatal and an
  # empty one silently drops the child's ownership fence.
  local out rc=0 number rel reservation_id="" reservation_generation=""
  local selection selection_rc=0 selection_state unknown_state
  # Everything that can refuse a launch because of how this machine is
  # configured is asked here, above the selector. Below this point the tick holds
  # a claim on the shared board, and a claim is not something the line can give
  # back on its own. The selector refuses the same two things, but as an empty
  # result with exit 2, which this script reads as "nothing to do"; without these
  # checks a typo in a settings file looks exactly like a queue that has run dry,
  # and a folder outside the checkout looks like a phase that launched and died.
  if ! roadmap_preflight; then
    log "$PREFLIGHT_REASON; refusing to launch"
    return 1
  fi
  if [ "${1:-}" = "--self-test" ] && [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    out="$(selector_run)" || rc=$?
    if [ "$rc" -ne 0 ]; then
      log "no uncompleted phase (next_phase rc=$rc)"
      return 0
    fi
    number="${out%%$'\t'*}"; rel="${out#*$'\t'}"
    reservation_id=""; reservation_generation=""
  else
    if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
      # Replay the own-claim recovery path with no board and no real takeover.
      # DRIVER_STUB_COORDINATION is "fence" for a takeover that returns a new
      # fence, or "nofence" for one that returns none. Only the selection and
      # the takeover are stubbed; the case below, the fence handling, and the
      # launch are the real code. This is the path a plain --self-test skips,
      # which is how an unbound reservation_id shipped unnoticed.
      log "self-test: stubbing coordination ($DRIVER_STUB_COORDINATION)"
      local stub_phase=100010
      # A fabricated selection has to look like a real one, and a real one
      # carries the launch path the driver now reads from the result. Its shape
      # is the whole of what matters here: DRIVER_STUB_RC means the launch is
      # stubbed and no file is opened, and a path taken from this machine's real
      # roadmap would make the case depend on which phases it happens to have.
      local stub_dir stub_path
      stub_dir="$(roadmap_dir)"
      stub_path="${stub_dir#"$REPO"/}/phase-$stub_phase-stub-fixture.md"
      selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","path":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7"}' \
        "$stub_phase" "$stub_path" "$PHASE_MACHINE_ID")"
      selection_state=WAIT_FOR_CLAIM
      if [ "${DRIVER_STUB_COORDINATION:-}" = "nopath" ]; then
        # A claim with no launch path in the result. The driver must refuse
        # rather than go looking for the file itself.
        selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7"}' \
          "$stub_phase" "$PHASE_MACHINE_ID")"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "review" ]; then
        # A selection that also carries two phases waiting on a pull request:
        # one merged and one not. The merge check must run on this selection and
        # must not be stopped by the unmerged one, or the merged one is stranded.
        selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","path":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7","awaiting_review":[{"phase":"100010","branch":"feature/review-merged","pull_request":"https://example/pr/1"},{"phase":"100011","branch":"feature/review-open","pull_request":"https://example/pr/2"}]}' \
          "$stub_phase" "$stub_path" "$PHASE_MACHINE_ID")"
        confirm_pending_reviews "$selection"
        [ "$PENDING_PUBLISHED" -eq 0 ] || log "self-test: $PENDING_PUBLISHED phase(s) moved to published; recomputing the selection"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "awaiting" ]; then
        # A selector that still stops on an open pull request. The driver and
        # the selector must not disagree about the queue, so reaching this means
        # the two files are from different versions and the driver halts rather
        # than proceeding on a queue it does not believe.
        selection="$(printf '{"state":"AWAITING_REVIEW","phase":"100010","branch":"feature/review-open","pull_request":"https://example/pr/1","owner":"%s"}' \
          "$PHASE_MACHINE_ID")"
        selection_state=AWAITING_REVIEW
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "unavailable" ]; then
        # A board that could not be reached or committed to, so the selector
        # answers with its own reason instead of a selection. No other stub value
        # can reach this branch, which is why the refusal had no reachable path
        # to be tested on. The reason is multi-line on purpose: the real one is a
        # wrapped git message, and a fix that only coped with a single line would
        # pass here and still lose the reason on the machine that stopped.
        selection="$(printf '{"state":"COORDINATION_UNAVAILABLE","error":"coordination state commit failed: unable to auto-detect email address (got 100010@driver-stub)\\n\\nfatal: unable to auto-detect email address"}')"
        selection_state=COORDINATION_UNAVAILABLE
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "unknown" ]; then
        # A state this case statement has no case for, carrying a reason. Nothing
        # else produces it, and the catch-all is exactly the branch that has to
        # name a reason nobody anticipated.
        selection="$(printf '{"state":"SOME_FUTURE_STATE","error":"the selector reported a state this driver has no case for"}')"
        selection_state=SOME_FUTURE_STATE
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "splitstate" ]; then
        # The same catch-all, with a state name carrying a real line feed. The
        # selector only ever writes fixed names today, so this is latent rather
        # than live, and it is the reason the state name is flattened and capped
        # beside the reason instead of being interpolated as it arrived: an
        # unflattened one would break this line in two, which is the whole defect
        # this code exists to close, reached through the other field of the same
        # answer.
        selection="$(printf '{"state":"SOME_FUTURE\\nSTATE","error":"the selector reported a state this driver has no case for"}')"
        selection_state="$(printf 'SOME_FUTURE\nSTATE')"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "silent" ]; then
        # The unavailable state with no reason field at all. A selector that
        # refuses without saying why is a real shape, and the two places that
        # answer this state have to read the same way when it happens: one named
        # the state and stopped there, and the other grew an empty pair of
        # brackets because it assumed a reason was always there.
        selection="$(printf '{"state":"COORDINATION_UNAVAILABLE"}')"
        selection_state=COORDINATION_UNAVAILABLE
      fi
    else
      # The merge check and the tidy step run before the checkout sync, and the
      # order is the whole of this fix.
      #
      # Merging a pull request deletes its head branch, so the checkout finalize
      # left it on has no upstream left to fetch. sync_one refuses, and the two
      # steps that would have moved the checkout back both ran after that
      # refusal: the merge check needs the selection, and the selection came
      # after the gate. So every merged phase whose branch was deleted stopped
      # the line permanently, and only a human checking out master by hand
      # restarted it. This has now happened twice, which is why it is a fix and
      # not an observation.
      #
      # Both steps before the sync need nothing the sync would have produced.
      # The tidy step reads a receipt and asks for a clean tree and a local
      # integration branch; the merge check asks GitHub about a pull request and
      # reads receipts off disk. The selection needs the private checkout
      # synced, which is what the pre-sync tidy already guarantees, so running it
      # here rather than after the gate costs nothing and unblocks the gate.
      #
      # The gate itself is kept. A checkout that cannot be synced for any other
      # reason — a dirty tree, an unresolvable conflict, a missing remote — still
      # refuses, because nothing here can tell that fault from the one this
      # ordering fixes.
      tidy_published_checkout || return 1
      selection="$(coordination_select)" || selection_rc=$?
      if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 2 ] \
          && [ "$selection_rc" -ne 3 ]; then
        log "coordination state unavailable; refusing to launch (next_phase rc=$selection_rc)"
        return 1
      fi
      selection_state="$(printf '%s' "$selection" | selection_value state)"
      # A phase whose pull request is merged is a finished phase, and this tick's
      # selection was computed without that fact. Confirm what merged first, so
      # the line never launches a phase the board already considers done.
      #
      # A selection that is already a reservation is never recomputed. This tick
      # holds the phase the selector picked, so asking the selector again can
      # only answer WAIT_FOR_CLAIM for that same phase: it is now the board's
      # lowest claimable one and this host owns it. With recover-own-claim off,
      # the driver reads that as another host holding the phase, refuses the
      # launch, and leaves a claim with no run behind it that every later tick
      # then waits on. The published phases are older than the reserved one, so
      # publishing them does not move which phase is next; it moves the base the
      # phase branches from, and the branch cut at launch resolves that base
      # fresh.
      if [ -n "$selection_state" ]; then
        confirm_pending_reviews "$selection"
        if [ "$PENDING_PUBLISHED" -gt 0 ]; then
          log "$PENDING_PUBLISHED phase(s) moved to published"
          # A phase the merge check just published may have been the phase this
          # checkout was sitting on, so the tidy step runs rather than being left
          # to the launch path below: the sync gate that follows cannot succeed
          # on a branch whose remote copy is gone. It runs whether or not the
          # selection is recomputed.
          tidy_published_checkout || return 1
          if [ "$selection_state" = "RESERVED" ]; then
            log "keeping this tick's reservation of phase $(printf '%s' "$selection" | selection_value phase)"
          else
            # Nothing is reserved yet, so a recomputation can only move the line
            # to a better phase, and there is no claim to lose.
            log "recomputing the selection"
            selection_rc=0
            selection="$(coordination_select)" || selection_rc=$?
            if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 2 ] \
                && [ "$selection_rc" -ne 3 ]; then
              log "coordination state unavailable after the merge check; refusing to launch"
              return 1
            fi
            selection_state="$(printf '%s' "$selection" | selection_value state)"
          fi
        fi
      fi
      # The gate, after the steps that recover from a dead branch. This is the
      # same fail-closed check as before, on a checkout that is no longer
      # stranded on one.
      if ! "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode start \
          >"$RUN_DIR/coordination-sync.json" 2>/dev/null; then
        log "coordination checkout sync failed; refusing to launch (see $RUN_DIR/coordination-sync.json)"
        return 1
      fi
    fi
    # A selection the board can answer with means an earlier outage is over, so
    # its marker goes and the next outage speaks for itself. This sits after
    # both the stubbed and the real branch and after the merge check, so every
    # path that produces a selection re-arms it: an outage marker only a stub
    # could clear would leave the first real outage silent for ever after. The
    # unavailable state is the one that keeps the marker, which is what makes
    # the notification once per outage rather than once per tick. The first-seen
    # time goes with it, because a stamp beside no marker would be read as the
    # start of an outage that has already ended.
    if [ "$selection_state" != "COORDINATION_UNAVAILABLE" ]; then
      rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
      rm -f "$RUN_DIR/coordination-outage-first-seen"
    fi
    report_needs_decision "$selection"
    case "$selection_state" in
      RESERVED)
        number="$(printf '%s' "$selection" | selection_value phase)"
        rel="$(printf '%s' "$selection" | selection_value path)"
        reservation_id="$(printf '%s' "$selection" | selection_value reservation_id)"
        reservation_generation="$(printf '%s' "$selection" | selection_value generation)"
        log "reserved phase $number ($rel) for $PHASE_MACHINE_ID"
        ;;
      WAIT_FOR_CLAIM)
        wait_phase="$(printf '%s' "$selection" | selection_value phase)"
        wait_owner="$(printf '%s' "$selection" | selection_value owner)"
        wait_status="$(printf '%s' "$selection" | selection_value status)"
        if [ "$RECOVER_OWN_CLAIM" = 1 ] \
            && [ "$wait_owner" = "$PHASE_MACHINE_ID" ] \
            && [ "$wait_phase" != "" ]; then
          # Our own claim, left behind by a run that crashed, was stopped, or
          # ended blocked. Take it back explicitly so the phase can finish.
          # A claim held by any other host still stops the line.
          log "recovering own claim on phase $wait_phase (status=$wait_status) for $PHASE_MACHINE_ID"
          local recover_rc=0
          if [ "${DRIVER_STUB_COORDINATION:-}" = "nofence" ]; then
            # A takeover that reports success but hands back no fence.
            printf '{"state": "OK"}\n' >"$RUN_DIR/claim-recovery-$wait_phase.json"
          elif [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
            printf '{"state": "OK", "reservation": {"reservation_id": "rsv-recovered-stub", "generation": 8, "machine_id": "%s", "phase": "%s", "status": "claimed"}}\n' \
              "$PHASE_MACHINE_ID" "$wait_phase" >"$RUN_DIR/claim-recovery-$wait_phase.json"
          elif ! "$PYTHON" "$ARGUS/phase_reservations.py" takeover \
              "${ARGUS_CONFIG_ARGS[@]}" \
              --repo-root "$REPO" --phase "$wait_phase" \
              --machine-id "$PHASE_MACHINE_ID" \
              --expected-reservation-id "$(printf '%s' "$selection" | selection_value reservation_id)" \
              --expected-generation "$(printf '%s' "$selection" | selection_value generation)" \
              --confirm >"$RUN_DIR/claim-recovery-$wait_phase.json" 2>&1; then
            recover_rc=1
          fi
          if [ "$recover_rc" -eq 0 ]; then
            number="$wait_phase"
            # The launch path comes from the work source, because the source is
            # what named the claim and is the only thing that knows where its
            # items live. This used to be a glob of the roadmap folder done here,
            # which put roadmap knowledge back into the driver for no reason: an
            # issue source's recovery would have been searching a roadmap for a
            # file named after an issue. Fail closed rather than fall back to a
            # guess if a result ever arrives without one.
            rel="$(printf '%s' "$selection" | selection_value path)"
            if [ -z "$rel" ]; then
              log "claim recovery for $wait_phase returned no launch path from the work source; refusing to launch"
              return 1
            fi
            # The takeover fences the old generation and issues a new one. The
            # child driver needs that new fence, not the stale one we matched
            # on, so read it back rather than passing the pre-takeover values.
            reservation_id="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reservation",{}).get("reservation_id",""))' \
              "$RUN_DIR/claim-recovery-$wait_phase.json")"
            reservation_generation="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reservation",{}).get("generation",""))' \
              "$RUN_DIR/claim-recovery-$wait_phase.json")"
            if [ -z "$reservation_id" ] || [ -z "$reservation_generation" ]; then
              log "claim recovery for $wait_phase returned no new fence; refusing to launch (see $RUN_DIR/claim-recovery-$wait_phase.json)"
              return 1
            fi
            log "recovered phase $number ($rel) on $reservation_id generation $reservation_generation; continuing"
          else
            log "claim recovery for phase $wait_phase failed; see $RUN_DIR/claim-recovery-$wait_phase.json"
            return 1
          fi
        else
          log "WAIT_FOR_CLAIM for phase $wait_phase (owner=$wait_owner status=$wait_status); no launch"
          return 0
        fi
        ;;
      COORDINATION_INCONSISTENT)
        log "coordination state conflicts with completion evidence; no launch"
        return 1
        ;;
      COORDINATION_UNAVAILABLE)
        # The board could not be reached or committed to, so the selector could
        # not answer at all. Its whole answer is captured in a shell variable that
        # dies with this tick, the reason is a free-text field inside it, and the
        # selector writes nothing to stderr on this path, so without this the
        # operator had a state name and no reason. The reason is quoted in the
        # log line and the whole answer is kept on disk beside it, which is what
        # the sibling refusals here already do. An absent reason reads without one,
        # the same as it did before the state had a case of its own.
        unavailable_error="$(printf '%s' "$selection" | selection_value error)"
        printf '%s\n' "$selection" >"$RUN_DIR/coordination-selection.json"
        if [ -n "$unavailable_error" ]; then
          unavailable_error="$(cap_length "$(flatten_one_line "$unavailable_error")" 500)"
          log "COORDINATION_UNAVAILABLE ($unavailable_error); refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
        else
          log "COORDINATION_UNAVAILABLE; refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
        fi
        notify_coordination_outage "$unavailable_error"
        return 1
        ;;
      BLOCKED_ON_GATE)
        # A plan that owes the owner a product decision is not runnable. The
        # selector stops here and reserves nothing, so the line waits on a
        # person recording the decision rather than on an agent discovering it
        # mid-run. Exiting 0 stops the driver without failing it: nothing is
        # broken, the queue is waiting on a human.
        gate_phase="$(printf '%s' "$selection" | selection_value phase)"
        gate_error="$(printf '%s' "$selection" | selection_value error)"
        gate_rel="$(printf '%s' "$selection" | selection_value path)"
        log "BLOCKED_ON_GATE for phase $gate_phase ($gate_error); no launch, no reservation"
        log "record the decision in $gate_rel, then re-run the driver"
        return 0
        ;;
      NO_PHASE)
        log "no uncompleted phase"
        return 0
        ;;
      AWAITING_REVIEW)
        # Kept as a case, not as dead weight: a selector that still stops here
        # means next_phase.py and this script disagree about the queue, and the
        # line stopping for that reason is a bug worth naming rather than
        # silently working around. The merge check has already run by the time
        # this is reached, so a phase reported here has genuinely not merged.
        review_phase="$(printf '%s' "$selection" | selection_value phase)"
        review_branch="$(printf '%s' "$selection" | selection_value branch)"
        review_pr="$(printf '%s' "$selection" | selection_value pull_request)"
        log "phase $review_phase is waiting for review on $review_branch, but the selector stopped on it"
        log "the selector should skip an open pull request and continue; this phase now blocks every later one"
        [ -z "$review_pr" ] || log "  pull request: $review_pr"
        [ -z "$STALL" ] || return 0
        notify "Phase $review_phase is waiting for a human, and the selector is holding the whole line behind it.

Branch: $review_branch
Pull request: ${review_pr:-none recorded}"
        return 1
        ;;
      *)
        # A state nobody anticipated is the case where a reason matters most, so
        # the error field is named when the selector sent one. The state name
        # stays in the line either way, so an empty field reads as it did before.
        # The state name goes through the same two helpers as the reason, because
        # the selector's answer is free text in that field too: a newline in it
        # would forge a second entry exactly as one in the reason would.
        unknown_state="$(cap_length "$(flatten_one_line "$selection_state")" 500)"
        unknown_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unknown_error" ]; then
          unknown_error="$(cap_length "$(flatten_one_line "$unknown_error")" 500)"
          log "unexpected coordination result '$unknown_state' ($unknown_error); refusing to launch"
        else
          log "unexpected coordination result '$unknown_state'; refusing to launch"
        fi
        return 1
        ;;
    esac
  fi
  log "next phase: $number ($rel)"

  # Before the checkout is cut over, land the previous phase's closing run-state
  # writes. A run that ended without its own clean leaves them dirty, and the
  # publish at the end of this phase would refuse on them.
  gitsync_land_leftovers

  # The public checkout is put on the phase's own branch before the launch, from
  # the integration branch the sync just brought up to date, and it carries the
  # phase's staged work across untouched. A failure here is fail-closed: a
  # launch onto the integration branch would put unreviewed product code where
  # other developers can pull it.
  #
  # The private checkout is deliberately left on the integration branch. It
  # holds run state and phase notes, which the operator reads on master, and a
  # phase that waits for a human to merge it stops the line for no gain. The
  # helper reports that as stays-on-integration-branch, so the two checkouts stay
  # distinguishable in the phase's own record.
  if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    local roadmap_name
    roadmap_name="$(basename "$rel" .md)"
    # Return the checkout to the integration branch when the phase it is on is
    # already recorded as published. Its own step, on its own tick, because the
    # merge check writes the receipt before it moves the branch: when the move
    # fails, the receipt says published, the selector stops reporting the phase
    # as awaiting review, and nothing would ever retry it. That left the
    # checkout on a dead branch and every later phase refusing to launch on it.
    # The tidy step reads the receipt and finishes the job, so the two outcomes
    # can succeed and fail independently.
    #
    # This is the same call _drive() now makes before the sync gate, and it is
    # kept here because this is the point where the phase branch is about to be
    # cut: a published phase's branch must not still be the one checked out when
    # the next phase starts from it. Running it in both places is deliberate —
    # the step is idempotent, and each place covers a case the other does not.
    tidy_published_checkout || return 1
    # The exit code is captured by running the command and reading $? straight
    # after. Putting it behind `if ! cmd; then rc=$?` cannot work: the `!`
    # resets $? to 0 before the body runs, so the guard below saw success even
    # when the sync refused, and the phase launched anyway.
    # --roadmap-dir as well as --roadmap: the first names the phase file's
    # behaviour, and the second names the folder that file was chosen from.
    # gitsync's branch mode scans that folder to refuse a branch name another
    # phase in it would also earn, so without it the check runs against the
    # derived folder and a collision inside a configured one goes unseen.
    "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      --mode branch --phase "$number" --roadmap "$roadmap_name" \
      --roadmap-dir "$(roadmap_dir)" \
      >"$RUN_DIR/phase-branch.json" 2>/dev/null
    local branch_rc=$?
    if [ "$branch_rc" -ne 0 ]; then
      log "could not put phase $number on its own branch; refusing to launch (see $RUN_DIR/phase-branch.json)"
      return 1
    fi
    # The two branches are read per checkout by role, from the action each one
    # reported. The receipt's top-level "branch" is the name that was asked
    # for, which is the same string for both checkouts, so printing it as
    # though it were the public checkout's branch reported a phase branch for a
    # checkout that was still sitting on master.
    log "phase $number: $(branch_state_line "$RUN_DIR/phase-branch.json")"
    # The branch may have been cut from an open predecessor rather than from the
    # integration branch, and the integration branch may have moved since the
    # launch-time sync. Pull it in now so the phase starts from a base that
    # contains both. A conflict here is not fatal and is not this script's to
    # resolve: the helper leaves it in place and names the paths, and the phase
    # agent finishes it. Refusing to launch would put the human back on the
    # critical path for a conflict the agent is there to fix.
    "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode resync \
      >"$RUN_DIR/phase-resync.json" 2>/dev/null
    local resync_rc=$?
    log "phase $number: $(resync_state_line "$RUN_DIR/phase-resync.json")"
    if [ "$resync_rc" -ne 0 ]; then
      log "the integration branch did not merge cleanly into phase $number; the phase agent finishes it (see $RUN_DIR/phase-resync.json)"
    fi
  fi

  local envs=""
  # The stage-title hook (pre_step) needs the session and the phase number; glide
  # passes neither, so both travel through the launch environment. The child
  # driver loads the same configuration file, named on its own command line.
  envs="$envs CLIO_TMUX_SESSION='$SESSION' CLIO_PHASE_NUMBER='$number'"
  [ -z "${DISABLE_NOTIFY:-}" ] || envs="$envs DISABLE_NOTIFY='$DISABLE_NOTIFY'"
  [ -z "${DISABLE_DISCORD:-}" ] || envs="$envs DISABLE_DISCORD='$DISABLE_DISCORD'"
  [ -z "${DRIVER_STUB_RC:-}" ] || envs="$envs DRIVER_STUB_RC='$DRIVER_STUB_RC'"
  [ -z "${DRIVER_STUB_SLEEP:-}" ] || envs="$envs DRIVER_STUB_SLEEP='$DRIVER_STUB_SLEEP'"
  [ -z "${DRIVER_STUB_WAIT_FILE:-}" ] || envs="$envs DRIVER_STUB_WAIT_FILE='$DRIVER_STUB_WAIT_FILE'"
  [ -z "${DRIVER_STUB_READY_FILE:-}" ] || envs="$envs DRIVER_STUB_READY_FILE='$DRIVER_STUB_READY_FILE'"
  [ -z "$reservation_id" ] || envs="$envs CLIO_RESERVATION_ID='$reservation_id'"
  [ -z "$reservation_generation" ] || envs="$envs CLIO_RESERVATION_GENERATION='$reservation_generation'"
  local self_quoted number_quoted rel_quoted config_arg="" quoted setting
  printf -v self_quoted '%q' "$SELF"
  printf -v number_quoted '%q' "$number"
  printf -v rel_quoted '%q' "$rel"
  for setting in "${ARGUS_CONFIG_ARGS[@]}"; do
    printf -v quoted '%q' "$setting"
    config_arg="$config_arg $quoted"
  done
  local launch="${envs:+$envs }bash $self_quoted$config_arg --session $number_quoted $rel_quoted"

  # Geometry: the session is created once at TMUX_WIDTH x TMUX_HEIGHT with
  # `window-size manual`, so it holds that size even when a smaller display
  # attaches. runner.py copies the pane size into each harness pty, so this is
  # what the agent actually works at. The phase window is then sized explicitly,
  # because new-window has no -x/-y and inherits the session size instead.
  #
  # The session is ensured first and never killed, so a display attached to the
  # idle window survives the launch. 9>&- drops the flock fd, as before.
  9>&- tmux_session_ensure \
    || { notify "phase $number failed to create tmux session '$SESSION'"; return 1; }
  # Set the title BEFORE the window opens. The child sets its own titles as it
  # runs, and anything written after the window opens races with them: a parent
  # "starting" landing after the child's "done" would leave the display showing
  # a phase that already finished.
  tmux_set_title "phase $number starting"
  9>&- tmux_phase_window_open "$launch" "$RUN_DIR/session-$number.log" \
    || { notify "phase $number failed to launch in tmux"; return 1; }
  printf '%s\n' "$number" >"$CURRENT"
  # The window stays named 'phase'. Renaming it to carry the phase number would
  # break every target string in phase-tmux.sh that looks it up by name, and
  # the phase number is already in the title and the log. The stage hook
  # renames it per stage instead, which is the display-facing label.
  tmux_phase_window_select
  log "launched phase $number in tmux '$SESSION' window 'phase'"
}

drive() {
  local rc=0
  mkdir -p "$RUN_DIR"
  log "configuration: driver=$DRIVER repo=$REPO roadmap=$(roadmap_dir) config=${ARGUS_CONFIG_FILE:-none}; $CONFIG_PRECEDENCE"
  if command -v flock >/dev/null 2>&1; then
    _drive "${1:-}" || rc=$?
  else
    mkdir -p "$RUN_DIR"
    # Named without `local`, and released explicitly below as well as from the
    # trap, because a name scoped to this function is already unset by the time a
    # subshell's EXIT trap runs on bash older than 4.4 when this function was
    # called inside a command substitution. The trap then removed nothing, the
    # directory stayed, and every later tick in that process found the lock held
    # and skipped while reporting success.
    PORTABLE_LOCK="$RUN_DIR/driver.lock.d"
    if ! mkdir "$PORTABLE_LOCK" 2>/dev/null; then
      log "another driver instance holds the portable lock; skip"
      heartbeat
      return 0
    fi
    (
      # EXIT only. A trap on a signal without an exit in it would take the lock
      # away while this tick was still running, which is the one thing the lock is
      # for. A tick killed outright therefore leaves the directory behind, which
      # is a limit of this path and not of the flock path above.
      trap 'rmdir "$PORTABLE_LOCK" 2>/dev/null || true' EXIT
      _drive "${1:-}" || rc=$?
      exit "$rc"
    )
    rc=$?
    # Taken out here rather than left to the trap. The trap is the safety net for
    # a tick killed by a signal, and it cannot be the only thing that releases the
    # lock: a tick that returns normally is the common case, and it must not depend
    # on a trap whose scope the caller can change by calling this function from
    # inside a substitution.
    rmdir "$PORTABLE_LOCK" 2>/dev/null || true
  fi
  heartbeat
  return "$rc"
}

stop() {
  mkdir -p "$RUN_DIR"
  printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"
  # Ctrl-C must reach the phase window. Targeting the session would send it to
  # whichever window is current, which may be the idle status display.
  if tmux_phase_window_alive; then
    tmux_phase_window_interrupt
    log "stop: sent Ctrl-C to '$SESSION:phase' and wrote halt marker"
  elif tmux_session_exists; then
    log "stop: wrote halt marker (session idle, no phase window)"
  else
    log "stop: wrote halt marker (no live session)"
  fi
}

# Wait for the phase window to disappear. The session itself is persistent and
# is never waited on.
wait_phase_window_gone() {
  local i=0
  while tmux_window_exists; do
    i=$((i + 1)); [ "$i" -gt 60 ] && break
    sleep 0.25
  done
  sleep 0.3
}

# A temporary directory whose shell scripts this host will actually run.
#
# The self-test copies the driver's own code into a fixture root and hands a
# path inside it to tmux, which execs the file directly. That only works when the
# filesystem permits execution: some hosts mount the default temporary directory
# `noexec`, and there tmux cannot start the fixture's idle script at all. The
# refusal names the script, so it reads as a driver fault rather than a property
# of the mount.
#
# The candidates are tried in order and the first one that runs an executable
# file is used, which is the same rule the repository's Python suites follow.
# A host with none of them gets a stated skip naming the mount, because a skip
# that says why cannot be mistaken for a passing check.
# Asks Argus's bundled helper for the directory rather than restating its
# list of candidates here. One rule, one place: a second copy in this repository is
# a second thing to update when the first moves, and this one lives in another
# checkout entirely, so it is the copy nobody would remember.
#
# The directory is created by that helper but not removed by it. A finaliser bound
# to the helper's process would delete the directory the moment this function
# returned, so the self-test owns the removal instead; see `driver_self_test`.
selftest_tmpdir() {
  local helper="$ARGUS/exec_tempdir.py" leftover
  # Fixtures abandoned by an earlier run are removed here, so an interrupted run is
  # cleaned up by the next one rather than waiting for a reboot. Keyed on this file's
  # own prefix and limited to directories, so nothing outside the fixtures can be
  # caught by it.
  #
  # Only ones older than an hour, because two self-tests on one machine would
  # otherwise delete each other's fixture: this sweep cannot tell an abandoned
  # directory from one another run is using right now, and a fresh one is
  # indistinguishable from that. The age is what tells them apart.
  for leftover in /tmp/argus-selftest-* /dev/shm/argus-selftest-* \
                  "$REPO/target"/argus-selftest-*; do
    [ -d "$leftover" ] || continue
    # `find` prints the directory only when it is older than the age asked for, so
    # an empty answer means too young to be abandoned.
    [ -n "$(find "$leftover" -maxdepth 0 -mmin +60 2>/dev/null)" ] || continue
    rm -rf "$leftover" 2>/dev/null
  done
  # On stderr, not stdout: the caller captures this function's stdout to take the
  # directory path, so anything printed there would be swallowed into the path.
  # At this point the run directory may not exist yet, and a skip whose reason
  # never reaches the reader is indistinguishable from a skip because the host has
  # nowhere to run a script.
  if [ ! -f "$helper" ]; then
    printf 'self-test: the bundled temporary directory helper is missing: %s\n' "$helper" >&2
    return 1
  fi
  "$PYTHON" - "$helper" <<'PY' 2>/dev/null
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
try:
    from exec_tempdir import make_exec_dir
except ImportError:
    raise SystemExit(1)
directory = make_exec_dir()
if not directory:
    raise SystemExit(1)
# Left in place on purpose: the caller outlives this process and removes it.
print(directory)
PY
}

driver_self_test() {
  local fail=0 tmp selftest_wf
  if ! tmp="$(selftest_tmpdir)"; then
    echo "skip: no fixture directory could be built, so no check ran"
    echo "      (a directory has to permit running a shell script; a home directory is not"
    echo "       a candidate, because it is not scratch space. The reason above, if any,"
    echo "       names which of the two it was.)"
    # 77 is the conventional exit for "skipped, nothing ran", and it is not 0.
    # A caller that reads an exit status has to be able to tell a skip from a pass:
    # a run that executed no checks has verified nothing, and reporting it as a
    # pass is the same mistake in a quieter form.
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  # The fixture tree and every tmux session this run creates are removed on the
  # way out, and they are removed from a trap because a run that stops part way
  # never reaches the teardown at the end of the body. A session left behind keeps
  # an idle-phase monitor running that goes on forking after the run printed its
  # last line, and one such session per run is what a list of sessions turns into
  # for whoever is trying to find a stuck phase.
  #
  # The session names are this shell's own pid joined to the prefix each case
  # uses, so the trap can remove a session a case renamed into place and then died
  # before putting the previous name back.
  #
  # These three are named without `local` on purpose. The trap runs after
  # driver_self_test has returned, so a name scoped to that function would be gone
  # by the time the trap needed it, and an unset name under `set -u` would stop
  # the trap on its first line and leave the session behind again.
  SELFTEST_TMP="$tmp"
  export TMPDIR="$tmp"
  TMUX_TMPDIR="$tmp/tmux"
  mkdir -p "$TMUX_TMPDIR"
  export TMUX_TMPDIR
  SELFTEST_SESSION="ar-driver-selftest-$$"
  SELFTEST_REACHED_END=0
  selftest_teardown() {
    local rc=$? violations=""
    # A run that stopped before its last check reports failure even when the status
    # says otherwise.
    #
    # The status cannot be the signal, and the reason is worth writing down
    # because it is neither portable nor what it looks like. Measured on this
    # machine's bash 3.2.57 and on bash 5.3.20, with `set -euo pipefail` and a body
    # that stops early:
    #
    #   an unbound variable, no trap      bash 3.2 -> 1    bash 5.3 -> 1
    #   an unbound variable, EXIT trap    bash 3.2 -> 0    bash 5.3 -> 1
    #   an unbound variable, a trap that
    #     re-exits with the saved status  bash 3.2 -> 0    bash 5.3 -> 1
    #   a command that returned false     both -> 1
    #   an empty array expansion          bash 3.2 -> an error   bash 5.3 -> not one
    #
    # So on bash 3.2 an installed EXIT trap swallows the abort and reports 0, and
    # preserving the status inside the trap does not help. On bash 4.4 and later
    # the empty-array expansion that used to trigger this is not an error at all,
    # so that trigger is version-specific while the hole underneath it is not. A
    # flag the run sets for itself is the only mechanism that works on all of them.
    #
    # An `if` and not an `&&` chain, because a chain that ends false is itself a
    # failing command and `set -e` would stop the teardown on its first line,
    # which is the session leak this exists to stop. A status the run chose on
    # purpose, such as the skip code, is left alone.
    if [ "$rc" -eq 0 ] && [ "$SELFTEST_REACHED_END" -eq 0 ]; then
      rc=1
      echo "FAIL: the self-test stopped before its last check" >&2
    fi
    # The tripwire, here rather than at the end of the body. A check at the end of
    # the body is not run at all by the run that needs it, and the run that needs
    # it is the one that stops half way. This run is pointed at a throwaway
    # checkout, so nothing below should ever fire; it stays because a layout that
    # stops being honest has to say so rather than be found by whoever's tree
    # moved. See argus/selftest-support.sh.
    violations="$(selftest_real_state_violations 2>&1)" || rc=1
    if [ -n "$violations" ]; then
      printf '%s\n' "$violations" | while IFS= read -r line; do
        [ -n "$line" ] || continue
        printf 'FAIL: %s\n' "$line" >&2
      done
    fi
    # One machine-readable line for a caller that wants the verdict rather than
    # the prose. What it adds is that a stopped run, a moved checkout and a
    # failed assertion are all the same kind of event here, instead of one being
    # a line of text and the others being an exit code. The counts of passed and
    # failed checks stay in the ok and FAIL lines, which is where a reader looks
    # and where the fault harness already reads them.
    printf 'SELFTEST_RESULT {"exit":%s,"reached_end":%s,"violations":%s}\n' \
      "$rc" \
      "$([ "$SELFTEST_REACHED_END" -eq 1 ] && printf true || printf false)" \
      "$(printf '%s\n' "$violations" | grep -c . || true)"
    tmux kill-session -t "$SELFTEST_SESSION" 2>/dev/null || true
    tmux kill-session -t "ar-driver-coord-failure-$$" 2>/dev/null || true
    tmux kill-session -t "ar-hook-selftest-$$" 2>/dev/null || true
    tmux kill-session -t "ar-driver-strand-$$" 2>/dev/null || true
    # The dead-branch case creates this one and kills it only on its own happy
    # path, so a case that aborts before that leaves it running. It belongs in
    # the trap for the same reason the others do.
    tmux kill-session -t "ar-driver-dead-branch-$$" 2>/dev/null || true
    # A failing run keeps its fixture when asked, because the reason a case
    # failed is usually in a file the fixture owned and the teardown deletes.
    if [ "${SELFTEST_KEEP_TMP:-0}" = "1" ] && [ "$rc" -ne 0 ]; then
      printf 'selftest fixture kept: %s\n' "$SELFTEST_TMP" >&2
    else
      rm -rf "$SELFTEST_TMP"
    fi
    exit "$rc"
  }
  trap selftest_teardown EXIT HUP INT TERM
  SESSION="$SELFTEST_SESSION"
  SESSION_NAME="$SESSION"
  # Read the machine's real state before this run points itself anywhere, so the
  # exit trap has something to compare against. The comparison happens in the
  # trap, not at the end of this body; see selftest_teardown.
  selftest_real_state_capture
  # A throwaway checkout for the whole suite, built before the first case runs.
  #
  # This suite used to run its cases against the machine's real checkouts and
  # guard against reaching them two ways: a stub, and a check at the end of this
  # body. Both guards failed, and each failed in the way that hid the other. The
  # stub skipped the very step that moves a checkout, so the step was never
  # tested; the check sat at the end, so the run that most needed it, the one
  # that stopped half way, never reached it. Two runs of this suite moved a
  # developer's public checkout, and one pushed a branch to the production
  # remote, before either was recorded.
  #
  # So the suite no longer has a real checkout to reach. Every case from here on
  # runs against a fixture with its own public checkout, private checkout, board
  # and roadmap, and the driver is pointed at it before anything is invoked. The
  # tripwire stays, because a layout that stops being honest still has to be
  # reported, but it is now a net under a floor rather than the floor.
  BOARD_DIR=board-data
  selftest_root="$tmp/selftest-root"
  if ! selftest_fixture_root "$selftest_root" "$tmp/selftest-base"; then
    echo "FAIL: the self-test's throwaway checkout could not be built, so no case ran" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  REPO="$selftest_root"
  # The stall check measures a phase directory, and the phase number it reads comes
  # from `$CURRENT`. It has to be a directory this test created: pointing it at a
  # phase the checkout happens to have, or at one it does not, decides the answer by
  # what is on disk rather than by the code under test. An absent directory measures
  # zero and the check returns early, which is why this reported no stall on a
  # healthy host.
  #
  # Saved and restored like the fixture section further down, so this one stops
  # depending on being the last thing that touched it.
  selftest_wf="${WF:-}"
  # The fixture's own configuration, written here and read through the same
  # module a tick uses. Every value the cases use comes from that file, so the
  # suite carries no layout knowledge and no ambient setting of its own: an empty
  # environment is enough to run it.
  selftest_config="$tmp/selftest-config.jsonc"
  if ! selftest_write_config "$selftest_root" "$selftest_config" "$BOARD_DIR"; then
    echo "FAIL: the self-test's fixture configuration could not be written" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  if ! load_fixture_config "$selftest_config"; then
    echo "FAIL: the self-test's fixture configuration could not be read" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  WORK_SOURCE_FLAGS=(--source roadmap)
  # The driver's own state lives inside the configured run folder, so a child
  # tick, which derives the same path from the same file, writes its log, halt
  # marker and pid where this suite reads them.
  DRIVER_RUN_DIR="$WF/$DRIVER_STATE"
  RUN_DIR="$DRIVER_RUN_DIR"
  ARGUS="$REPO/$BOARD_DIR/argus"
  export PATH="$ARGUS/bin:$PATH"
  NOTIFIER="$ARGUS/phase_notifications.py"
  mkdir -p "$WF"
  # Pass the fixture config to each child tool. The probe below asks the
  # notifier to resolve its run folder using that same config.
  set_config_args
  load_board_override() {
    local board_path="$1" config_path="$tmp/board-override-config.json"
    selftest_write_config "$REPO" "$config_path" "$BOARD_DIR" "$board_path"
    load_fixture_config "$config_path"
  }

  # And that the telling worked. A fixture which points the driver at a
  # throwaway checkout must leave every tool reading the throwaway, and the only
  # way to know it does is to ask a tool where it is looking. The notifier is the
  # one tool here that answers that question on the command line, so it is asked
  # directly rather than inferred from a notification that happened to look right.
  if ! layout_probe="$("$PYTHON" - "$ARGUS" "${ARGUS_CONFIG_ARGS[@]}" <<'PROBE' 2>&1
import sys

sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[2:])
from argus.phase_notifications import default_run_json
print(default_run_json("phase-100010-probe.md").parent.parent)
PROBE
  )"; then
    layout_probe="error: $layout_probe"
  fi
  case "$layout_probe" in
    "$WF") echo "ok: the notifier reads the fixture's run state, not the machine's" ;;
    *)
      echo "FAIL: the notifier reads run state from $layout_probe while the fixture reads $WF"
      fail=1
      ;;
  esac
  # Keep the failed probe above as evidence, then restore the fixture settings
  # so later checks still run when the publication guard is deliberately broken.
  set_config_args
  DISABLE_NOTIFY=1
  DISABLE_DISCORD=1
  DRIVER_STUB_RC=0
  # Long enough that the phase window is still open for the checks below. The
  # phase now lives in a window that closes on exit, so a 1s stub could finish
  # before the first assertion ran.
  DRIVER_STUB_SLEEP=6
  export SESSION_NAME DRIVER_RUN_DIR DISABLE_NOTIFY DISABLE_DISCORD DRIVER_STUB_RC DRIVER_STUB_SLEEP
  RUN_DIR="$DRIVER_RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"
  STALL="$RUN_DIR/stalled"; CURRENT="$RUN_DIR/current"
  PIDFILE="$RUN_DIR/pid"
  NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
  mkdir -p "$RUN_DIR"
  echo "self-test session: $SESSION  state dir: $RUN_DIR"

  title_of() { tmux show-option -t "$SESSION" -v set-titles-string 2>/dev/null || true; }

  # No coordination stub here, and that is the point of the throwaway checkout
  # above. These cases used to run on `DRIVER_STUB_COORDINATION=fence` for a good
  # reason: a tick with no stub asks the real selector which phase is next and
  # then asks the sync helper to put the public checkout on that phase's branch,
  # and doing that to a developer's real checkout moved it, twice, before anyone
  # recorded that it could. The stub was the only thing standing between this
  # suite and someone's working tree, and it had a second cost nobody named: the
  # stub skips the very steps that move a checkout, so the steps a test most needs
  # to cover were the ones no test covered.
  #
  # With a fixture there is nothing left to protect, so the stub goes and the
  # cases run the production path for real: the real selector, the real
  # reservation, the real sync, the real branch. What they assert is unchanged.
  #
  # The cases further down still stub coordination, and legitimately: they inject
  # answers the selector cannot produce, such as a board that is unreachable or a
  # state name this driver has no case for. A stub for a shape no real run can
  # produce is a different thing from a stub standing in for the real thing.
  RECOVER_OWN_CLAIM=1; export RECOVER_OWN_CLAIM

  drive --self-test || true
  tmux_session_exists \
    && echo "ok: launched a session" || { echo "FAIL: session not created"; fail=1; }
  tmux_window_exists \
    && echo "ok: phase window opened" || { echo "FAIL: phase window not opened"; fail=1; }
  tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx -- idle \
    && echo "ok: idle window present" || { echo "FAIL: idle window missing"; fail=1; }
  geom="$(tmux_phase_window_geometry)"
  [ "$geom" = "${TMUX_WIDTH}x${TMUX_HEIGHT}" ] \
    && echo "ok: phase window geometry $geom" \
    || { echo "FAIL: geometry ${geom:-gone}, want ${TMUX_WIDTH}x${TMUX_HEIGHT}"; fail=1; }
  case "$(title_of)" in
    *"starting"*|*"running"*) echo "ok: running title set ($(title_of))" ;;
    *) echo "FAIL: running title not set, got '$(title_of)'"; fail=1 ;;
  esac
  drive --self-test || true
  grep -q "running; skip" "$LOG" \
    && echo "ok: busy run skipped" || { echo "FAIL: busy run not skipped"; fail=1; }
  sleep 1
  start_count="$(grep -c 'Phase .* started\.' "$LOG" || true)"
  [ "$start_count" = 1 ] \
    && echo "ok: one start notification logged" || { echo "FAIL: expected one start notification, got $start_count"; fail=1; }
  grep -q '\[clio\]' "$LOG" \
    && { echo "FAIL: old notification prefix remains"; fail=1; } \
    || echo "ok: notification prefix removed"

  wait_phase_window_gone
  grep -q "Phase .* has completed\." "$LOG" \
    && echo "ok: completion notification logged" || { echo "FAIL: completion notification missing"; fail=1; }

  # The whole point of the change: the session outlives the phase.
  tmux_session_exists \
    && echo "ok: session survives phase completion" \
    || { echo "FAIL: session died with the phase"; fail=1; }
  tmux_window_exists \
    && { echo "FAIL: phase window still present after completion"; fail=1; } \
    || echo "ok: phase window closed on completion"
  tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx -- idle \
    && echo "ok: idle window survives phase" || { echo "FAIL: idle window gone after phase"; fail=1; }
  case "$(title_of)" in
    *"done"*) echo "ok: completion title set ($(title_of))" ;;
    *) echo "FAIL: completion title not set, got '$(title_of)'"; fail=1 ;;
  esac
  # An idle session must not block the next tick. Count skips before and after
  # rather than grepping the whole log: an earlier legitimate skip (while the
  # first phase ran) is still in there and would mask a real regression.
  skips_before="$(grep -c 'phase window is running; skip' "$LOG" || true)"
  drive --self-test || true
  skips_after="$(grep -c 'phase window is running; skip' "$LOG" || true)"
  if [ "$skips_after" -gt "$skips_before" ]; then
    echo "FAIL: idle session blocked the line"; fail=1
  else
    echo "ok: idle session does not block the line"
  fi
  wait_phase_window_gone

  wait_phase_window_gone

  # A tick whose output is captured in a command substitution.
  #
  # The lock a tick takes is created inside the function that takes it, and a
  # shell drops a function's local variables as soon as the function returns. An
  # EXIT trap that removes the lock therefore cannot see it by the time the trap
  # runs in some shells, and the lock is left behind; every later tick then finds a
  # lock it did not take and skips, so the driver stops ticking while still
  # reporting success. That is the worst shape a scheduler failure can take, and
  # it is invisible from the log, which says only that a tick ran.
  #
  # A command substitution is the trigger, not decoration: it runs the body in a
  # subshell, and a subshell's trap runs where the enclosing function's locals are
  # already gone. Two ticks in one process is the observation that matters, so
  # this counts skips across the capture the same way the idle case above does,
  # and it separately confirms the captured tick really ran, because a tick that
  # did nothing would pass the skip check for the wrong reason.
  # The lock is asserted by the path that this host actually takes. On a host
  # with flock(1) the lock is an advisory lock on an open file descriptor, and
  # the file itself stays on disk after the tick releases it: a persisted
  # `driver.lock` is normal and not a leak. The portable path is what leaked —
  # it made a directory and removed it on exit — so its absence is asserted, and
  # the skip count below proves release on both paths. Growth of the log is what
  # proves the captured tick ran at all: a tick that decided there was nothing to
  # do writes a line and leaves, so the log grows either way, whereas a tick that
  # never started would leave it untouched and pass the lock check for the wrong
  # reason.
  sub_lines_before="$(wc -l <"$LOG" | tr -d '[:space:]')"
  _captured="$(drive --self-test 2>&1)" || true
  sub_lines_after="$(wc -l <"$LOG" | tr -d '[:space:]')"
  # Count the two lock messages and not the phase-window one. A tick that finds
  # an open phase window skips too, and that is correct behaviour, so counting it
  # here would fail the case for a reason that has nothing to do with the lock.
  sub_skips_before="$(grep -c 'holds the portable lock; skip\|holds the lock; skip' "$LOG" || true)"
  if [ "$sub_lines_after" -le "$sub_lines_before" ]; then
    echo "FAIL: the tick captured in a substitution did not run, so the lock check proves nothing"
    fail=1
  elif [ -e "$LOCK.d" ]; then
    echo "FAIL: a tick captured in a substitution left its portable lock behind ($LOCK.d)"
    fail=1
  else
    drive --self-test || true
    sub_skips_after="$(grep -c 'holds the portable lock; skip\|holds the lock; skip' "$LOG" || true)"
    if [ "$sub_skips_after" -gt "$sub_skips_before" ]; then
      echo "FAIL: a tick captured in a substitution left a lock that stopped the next tick"
      fail=1
    else
      echo "ok: a tick captured in a substitution leaves no lock behind"
    fi
  fi
  unset _captured
  wait_phase_window_gone

  # The teardown trap, on the path it exists for.
  #
  # The leak it closes was one orphaned tmux session per run, and that session's
  # idle monitor goes on forking for minutes after the run prints its last line,
  # which is how twenty runs of this suite made a machine unresponsive. A trap
  # that has only ever been reached on the successful path has not been tested at
  # all, and the successful path is the one that needed it least: the leak
  # happened when a run stopped half way and never reached the teardown at the end
  # of the body.
  #
  # So this runs the teardown the way a dying run runs it. A child creates a real
  # session and a real fixture folder, installs the same trap, and then stops
  # early the way this suite always has, on an unbound variable under `set -u`.
  # From here the question is whether either survived, which is the only thing
  # the trap promises.
  abort_name="ar-driver-selftest-abort-$$"
  abort_tmp="$tmp/abort-fixture"
  mkdir -p "$abort_tmp"
  (
    export SELFTEST_TMP="$abort_tmp"
    export SELFTEST_SESSION="$abort_name"
    # This child is standing in for a run that stops half way, and the whole
    # point is that the trap removes its fixture. A keep flag on the parent
    # would exempt it, and the case would pass without testing the trap.
    SELFTEST_KEEP_TMP=0
    export SELFTEST_KEEP_TMP
    SELFTEST_REACHED_END=0
    export SELFTEST_REACHED_END
    tmux new-session -d -s "$abort_name" 'sleep 30' 2>/dev/null || true
    trap selftest_teardown EXIT HUP INT TERM
    printf 'unreached %s\n' "${NO_SUCH_VARIABLE}"
  ) >/dev/null 2>&1 || true
  # The child has to be given the moment it needs to create the session and then
  # abandon it, or the question answered is "has one appeared yet" rather than
  # "does one survive".
  sleep 1
  if tmux has-session -t "$abort_name" 2>/dev/null; then
    echo "FAIL: the teardown left the session behind when a run stopped half way"
    tmux kill-session -t "$abort_name" 2>/dev/null || true
    fail=1
  elif [ -d "$abort_tmp" ]; then
    echo "FAIL: the teardown left the fixture folder behind when a run stopped half way"
    fail=1
  else
    echo "ok: a run that stops half way still has its session and fixture removed"
  fi
  unset abort_name abort_tmp

  # The tripwire's own failure path, which has never once been observed to fire.
  #
  # A check that has only ever printed its success line has not been tested, and
  # this repository has already shipped a check that quietly declined to run; the
  # habit of trusting one because it looks like it works is what that costs. So
  # the tripwire is made to see a change here and has to report it.
  #
  # The snapshot is falsified rather than a real checkout being moved. The real
  # git reads still run, so the comparison, the reporting and the exit status are
  # all exercised; what is skipped is mutating a working tree mid-suite, which
  # would put every later case at the mercy of this one. Two of the three signals
  # are checked this way, because they are three separate branches in the function
  # and one of them being unreachable would be invisible otherwise.
  tripwire_saved_public="$SELFTEST_REAL_PUBLIC_STATE"
  tripwire_saved_board="$SELFTEST_REAL_BOARD_STATE"
  tripwire_saved_runs="$SELFTEST_REAL_RUNS"
  tripwire_saved_marker="$SELFTEST_REAL_RUNS_MARKER"
  tripwire_probe_dir="$tmp/tripwire-probe-runs"
  mkdir -p "$tripwire_probe_dir"
  tripwire_missed=""
  for tripwire_signal in checkout board runstate; do
    case "$tripwire_signal" in
      checkout) SELFTEST_REAL_PUBLIC_STATE="master 0000000000000000000000000000000000000000 changed=[]" ;;
      board) SELFTEST_REAL_BOARD_STATE="0000000000000000000000000000000000000000" ;;
      runstate)
        SELFTEST_REAL_RUNS="$tripwire_probe_dir"
        SELFTEST_REAL_RUNS_MARKER="$tripwire_probe_dir/marker"
        : >"$SELFTEST_REAL_RUNS_MARKER"
        printf 'written after the marker\n' >"$tripwire_probe_dir/run.json"
        ;;
    esac
    if selftest_real_state_violations >"$tmp/tripwire-$tripwire_signal.out" 2>&1; then
      tripwire_missed="$tripwire_missed $tripwire_signal"
    fi
    rm -f "$tripwire_probe_dir/run.json"
  done
  SELFTEST_REAL_PUBLIC_STATE="$tripwire_saved_public"
  SELFTEST_REAL_BOARD_STATE="$tripwire_saved_board"
  SELFTEST_REAL_RUNS="$tripwire_saved_runs"
  SELFTEST_REAL_RUNS_MARKER="$tripwire_saved_marker"
  if [ -n "$tripwire_missed" ]; then
    echo "FAIL: the checkout tripwire did not report a change in:$tripwire_missed"
    for tripwire_signal in $tripwire_missed; do
      sed 's/^/      /' "$tmp/tripwire-$tripwire_signal.out" 2>/dev/null | head -4
    done
    fail=1
  else
    echo "ok: the checkout tripwire reports a moved checkout, a moved board and a written run state"
  fi
  unset tripwire_saved_public tripwire_saved_board tripwire_saved_runs \
        tripwire_saved_marker tripwire_probe_dir tripwire_missed tripwire_signal

  # Exercise the stall path without waiting for the real threshold. Use this
  # shell as the live process so the test does not race the short stub run.
  old_stale_after_sec="$STALE_AFTER_SEC"
  stall_test_phase=100000
  printf '%s\n' "$stall_test_phase" >"$CURRENT"
  printf '%s\n' "$$" >"$PIDFILE"
  # The directory the check measures is built here, and only the file inside it
  # carries the age. `check_stall` reads the newest mtime under
  # `$WF/phase-<number>`; a directory it never created measures nothing, the
  # check returns before it can notify, and the test reports a missing
  # notification for a host that is working exactly as designed.
  stall_dir="$WF/phase-$(printf '%06d' "$stall_test_phase")"
  mkdir -p "$stall_dir"
  : >"$stall_dir/activity"
  : >"$RUN_DIR/session-$stall_test_phase.log"
  STALE_AFTER_SEC=0
  # Age is measured in whole seconds and the comparison is strictly greater, so
  # the file has to be at least one second old before the check runs. Sleeping
  # before the check rather than after the write is what makes that true; writing
  # last would leave a measured age of zero and the check would correctly decline
  # to fire.
  sleep 1
  check_stall
  stall_count="$(grep -c 'Phase .* looks stalled\.' "$LOG" || true)"
  [ "$stall_count" = 1 ] && [ -f "$STALL" ] \
    && echo "ok: one stall notification logged" || { echo "FAIL: expected one stall notification, got $stall_count"; fail=1; }
  check_stall
  stall_count_after="$(grep -c 'Phase .* looks stalled\.' "$LOG" || true)"
  [ "$stall_count_after" = "$stall_count" ] \
    && echo "ok: stall notification is not repeated" || { echo "FAIL: stall notification repeated"; fail=1; }
  rm -rf "$stall_dir"
  rm -f "$STALL" "$CURRENT" "$PIDFILE"
  STALE_AFTER_SEC="$old_stale_after_sec"

  DRIVER_STUB_RC=1; export DRIVER_STUB_RC
  rm -f "$HALT"
  drive --self-test || true
  wait_phase_window_gone
  [ -f "$HALT" ] \
    && echo "ok: halt marker written" || { echo "FAIL: halt marker missing"; fail=1; }
  grep -q "Phase .* stopped\." "$LOG" \
    && echo "ok: halt notification logged" || { echo "FAIL: halt notification missing"; fail=1; }
  case "$(title_of)" in
    *HALTED*) echo "ok: halt title set ($(title_of))" ;;
    *) echo "FAIL: halt title not set, got '$(title_of)'"; fail=1 ;;
  esac

  drive --self-test || true
  grep -q "refusing to advance" "$LOG" \
    && echo "ok: refuses to advance while halted" || { echo "FAIL: advanced while halted"; fail=1; }
  tmux_window_exists \
    && { echo "FAIL: opened a phase window while halted"; fail=1; } \
    || echo "ok: no phase window opened while halted"

  # Exhausting the remedy rounds is not a verdict: a run that ends
  # remedy_escalated records its open findings, releases the claim, and writes
  # no halt marker, so the next tick runs the next phase. This exercises the
  # real handler, not a stubbed exit code.
  rm -f "$HALT"
  escalate_phase=100010
  escalate_dir="$WF/phase-$(printf '%06d' "$escalate_phase")"
  mkdir -p "$escalate_dir"
  printf '{"state":"remedy_escalated","events":[{"step":"approver","signal":"REMEDY_REJECTED: F-01 abc12345"}]}\n' \
    >"$escalate_dir/run.json"
  handle_escalation "$escalate_phase" "phase-$escalate_phase-escalation.md"
  [ -f "$escalate_dir/remedy-escalated.json" ] && [ ! -f "$HALT" ] \
    && echo "ok: escalation recorded without a halt marker" \
    || { echo "FAIL: escalation record or halt state wrong"; fail=1; }
  grep -q "exhausted its remedy rounds" "$LOG" \
    && echo "ok: escalation notification logged" \
    || { echo "FAIL: escalation notification missing"; fail=1; }
  rm -f "$HALT"

  # A terminal run the selector hands back must be restarted, not wedged: glide
  # refuses to rerun a finished run without --fresh, so the driver reads the
  # prior state and asks for a fresh start. A blocked run is resumable, and only
  # an absent marker has nothing to archive.
  terminal_probe="$RUN_DIR/terminal-state-probe.json"
  printf '{"state":"rejected"}\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "rejected" ] \
    && echo "ok: a rejected run is read as terminal and asks for --fresh" \
    || { echo "FAIL: a rejected run did not ask for --fresh"; fail=1; }
  printf '{"state":"blocked"}\n' >"$terminal_probe"
  [ -z "$(terminal_state_of_file "$terminal_probe")" ] \
    && echo "ok: a blocked run is left to resume, not restarted" \
    || { echo "FAIL: a blocked run was restarted"; fail=1; }
  printf 'not json\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "unreadable" ] \
    && echo "ok: an unreadable run marker still asks for --fresh" \
    || { echo "FAIL: an unreadable run marker did not ask for --fresh"; fail=1; }
  printf '{"other": 1}\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "unreadable" ] \
    && echo "ok: a marker with no state still asks for --fresh" \
    || { echo "FAIL: a marker with no state did not ask for --fresh"; fail=1; }
  rm -f "$terminal_probe"
  [ -z "$(terminal_state_of_file "$terminal_probe")" ] \
    && echo "ok: a phase with no run marker asks for no --fresh" \
    || { echo "FAIL: a missing marker asked for --fresh"; fail=1; }

  # Signal stop: Ctrl-C reaches the trap, which halts and notifies. This is the
  # case that proves Ctrl-C is aimed at the phase window: the idle window is
  # still there, and a session-scoped send-keys would have hit it instead.
  rm -f "$HALT"
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=20; export DRIVER_STUB_RC DRIVER_STUB_SLEEP
  drive --self-test || true
  sleep 0.5
  stop || true
  wait_phase_window_gone
  [ -f "$HALT" ] \
    && echo "ok: stop wrote halt marker" || { echo "FAIL: stop marker missing"; fail=1; }
  grep -q "Phase .* stopped by signal\." "$LOG" \
    && echo "ok: stop notification logged" || { echo "FAIL: stop notification missing"; fail=1; }
  tmux_session_exists \
    && echo "ok: session survives an operator stop" \
    || { echo "FAIL: session died on operator stop"; fail=1; }

  # The next case is about the real path refusing, so the stub comes off first: it
  # runs a tick with no stub precisely so the coordination sync is attempted, and a
  # stub would make the case pass without reaching the refusal it is written for.
  # The recovery flag goes back with it, so the cases after this one start from the
  # state they would have started from before the stub was introduced.
  unset DRIVER_STUB_COORDINATION
  RECOVER_OWN_CLAIM=0; export RECOVER_OWN_CLAIM

  # Coordination failure: the normal driver path must refuse before tmux launch.
  old_repo="$REPO"; old_board_dir="$BOARD_DIR"; old_wf="$WF"
  old_argus="$ARGUS"; old_notifier="$NOTIFIER"
  old_run_dir="$RUN_DIR"; old_lock="$LOCK"; old_halt="$HALT"; old_stall="$STALL"
  old_current="$CURRENT"; old_pid="$PIDFILE"; old_notify_broken="$NOTIFY_BROKEN"
  old_log="$LOG"; old_session="$SESSION"
  coord_root="$tmp/coordination-failure-root"
  coord_config="$tmp/coordination-failure-config.json"
  mkdir -p "$coord_root/$BOARD_DIR/plans" "$coord_root/$BOARD_DIR/history"
  cp "$PIPELINE" "$coord_root/$BOARD_DIR/flow.yaml"
  REPO="$coord_root"
  selftest_write_config "$coord_root" "$coord_config" "$BOARD_DIR" \
    "$tmp/absent-board-checkout"
  load_fixture_config "$coord_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
  # Use the bundled publisher so this case reaches the coordination sync.
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  set_config_args
  RUN_DIR="$WF/$DRIVER_STATE"
  mkdir -p "$RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
  CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
  NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
  SESSION="ar-driver-coord-failure-$$"
  drive || true
  if grep -q "coordination checkout sync failed" "$LOG" \
      && ! tmux_window_exists 2>/dev/null; then
    echo "ok: coordination failure refuses launch"; coord_fail_ok=1
  else
    echo "FAIL: coordination failure did not refuse launch"; coord_fail_ok=0; fail=1
  fi
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  load_fixture_config "$selftest_config"
  RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
  CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
  LOG="$old_log"; SESSION="$old_session"

  # A merged phase whose branch was deleted on the remote, with the checkout
  # still sitting on it. This is the stall: the sync gate refused because the
  # branch had no upstream left, and both steps that would have moved the
  # checkout back ran after that refusal. The fixture is asserted to have the
  # shape before the tick runs, because a fixture that failed to build the dead
  # branch would make every assertion below pass for the wrong reason — and the
  # case before this one proved that a green check can come from a fixture that
  # is not the state it claims to be.
  dead_root="$tmp/dead-branch-root"
  dead_run="$tmp/dead-branch-run"
  if selftest_fixture_root "$dead_root" "$tmp/dead-branch-base" \
      && selftest_fixture_strand_on_published_branch "$dead_root" \
          "$tmp/dead-branch-base" "feature/dead-branch-fixture"; then
    REPO="$dead_root"; BOARD_DIR=board-data
    ARGUS_CONFIG_FILE="$tmp/dead-branch-config.jsonc"
    selftest_write_config "$dead_root" "$ARGUS_CONFIG_FILE" "$BOARD_DIR"
    load_fixture_config "$ARGUS_CONFIG_FILE"
    WORK_SOURCE_FLAGS=(--source roadmap)
    ARGUS="$dead_root/$BOARD_DIR/argus"
    NOTIFIER="$ARGUS/phase_notifications.py"
    RUN_DIR="$WF/$DRIVER_STATE"; mkdir -p "$RUN_DIR"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    SESSION="ar-driver-dead-branch-$$"
    # A configuration file for this sub-fixture, so the child tick it launches
    # reads the dead-branch checkout rather than the suite's.

    # What the tick is about to find, read from the fixture rather than assumed.
    # All three halves matter, because the reported stall was the shape where all
    # three held: the checkout on the phase branch, the remote copy deleted, and
    # the local tracking ref still present so the sync reached its own fetch
    # rather than stopping at "no upstream".
    dead_branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
    dead_remote="$(git -C "$REPO" ls-remote --heads origin \
      'refs/heads/feature/dead-branch-fixture' | wc -l | tr -d ' ')"
    dead_tracking=gone
    git -C "$REPO" rev-parse --verify --quiet \
      origin/feature/dead-branch-fixture >/dev/null 2>&1 || dead_tracking=absent
    if [ "$dead_branch" = "feature/dead-branch-fixture" ] \
        && [ "$dead_remote" = 0 ] && [ "$dead_tracking" = gone ]; then
      echo "ok: the fixture has a phase branch whose remote copy is deleted"
    else
      echo "FAIL: the dead-branch fixture is not stranded (branch '$dead_branch', remote copy count $dead_remote, tracking ref $dead_tracking)"
      fail=1
    fi

    rm -f "$HALT" "$RUN_DIR/coordination-outage"
    rm -f "$RUN_DIR/coordination-outage-first-seen"
    # A plain tick, not `--self-test`. That flag routes the tick down the stubbed
    # selection, which is the path where the sync gate is skipped altogether — so
    # a case written against it would pass with the gate in either order, and
    # would have passed before the fix too. This is the whole case: the gate is
    # the code under test, so the case has to reach it.
    dead_rc=0
    drive >/dev/null 2>&1 || dead_rc=$?
    dead_now="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    # The line carried on, rather than merely moving the branch. Asserting the
    # branch alone would pass on a tick that tidied and then refused for another
    # reason, which is the shape of a partial fix; and asserting a launch alone
    # would pass on a tick that launched without recovering, which would leave
    # the next phase built on a dead branch. Both are checked.
    dead_refusals="$(grep -c 'coordination checkout sync failed\|could not return a published phase' "$LOG" || true)"
    dead_launched="$(grep -c 'launched phase' "$LOG" || true)"
    # `|| true` on the pipeline, and not only on each stage: a `grep` that matches
    # nothing exits non-zero, `pipefail` carries that out of the pipeline, and the
    # case aborts on the very tick the fix is supposed to have changed. The run
    # would then report the fixture as untested rather than as unrecovered.
    dead_next_phase="$( { grep -m1 -o 'launched phase [0-9]*' "$LOG" || true; } | head -1 | tr -d ' ')"
    if [ "$dead_refusals" = 0 ] && [ "$dead_launched" -gt 0 ] \
        && [ -n "$dead_next_phase" ] \
        && [ "$dead_now" != "feature/dead-branch-fixture" ]; then
      echo "ok: a merged phase whose branch was deleted no longer stops the line (exit $dead_rc, on '$dead_now', '$dead_next_phase' launched)"
    else
      echo "FAIL: the dead branch was not recovered (exit $dead_rc, now on '$dead_now', refusals $dead_refusals, launches $dead_launched, next phase '$dead_next_phase')"
      fail=1
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
  else
    echo "FAIL: the dead-branch fixture could not be built, so the stall was not tested"
    fail=1
  fi
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  load_fixture_config "$selftest_config"
  RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
  CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
  LOG="$old_log"; SESSION="$old_session"

  # The board answered and said it could not be reached. Everything the refusal
  # owes an operator is asserted here: the reason reaches the log line, the whole
  # answer reaches disk, the launch stays refused, the reason costs one log entry
  # rather than three, a long reason is cut with a count, and an outage speaks
  # once instead of once a tick. The catch-all and the dry-run copy of the same
  # case statement are asserted alongside, because each is its own code.
  #
  # The reason the stub carries is multi-line, because the real one is a wrapped
  # git message. A case that only fitted a single line would pass here and still
  # lose the reason on the machine that stopped, and the log is one entry per
  # line, so a newline that got through would forge a second entry.
  #
  # The halt marker is cleared so a tick does not stop at the halt check before it
  # ever reaches the stub. The one still here was written by the signal-stop case
  # above, which stops the driver and does not take the marker away again. The
  # outage marker is removed because this case counts its notifications and a
  # marker left behind would silence the first one.
  rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  rm -f "$HALT" "$RUN_DIR/coordination-selection.json"
  stub_reason='coordination state commit failed: unable to auto-detect email address (got 100010@driver-stub)\n\nfatal: unable to auto-detect email address'
  # The entry the refusal has to produce, built from the reason the stub carries
  # and the folder the answer is written to, so a change to either half is one
  # failing case and not two.
  unavailable_entry="COORDINATION_UNAVAILABLE ($stub_reason); refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  unavailable_rc=0
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || unavailable_rc=$?

  if grep -q "unable to auto-detect email address.*coordination-selection\.json" "$LOG"; then
    echo "ok: an unreachable board names its reason and the file holding the whole answer"
  else
    echo "FAIL: the refusal line named neither the selector's reason nor coordination-selection.json"; fail=1
  fi

  # The whole answer, not only the reason. The reason's own newlines have to
  # survive in the file, which is the difference between the file and the log
  # entry: the entry is capped at 500 characters, the file is not.
  selection_probe="$("$PYTHON" - "$RUN_DIR/coordination-selection.json" <<'PROBE' 2>&1 || true
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"unreadable ({exc})")
    raise SystemExit(1)
reason = doc.get("error", "")
carries = "yes" if "unable to auto-detect email address" in reason else "no"
print(f"state={doc.get('state', '')} newlines={reason.count(chr(10))} reason={carries}")
PROBE
)"
  case "$selection_probe" in
    "state=COORDINATION_UNAVAILABLE newlines=2 reason=yes")
      echo "ok: the whole selector answer is on disk, newlines and all" ;;
    *)
      echo "FAIL: coordination-selection.json does not hold the selector's whole answer ($selection_probe)"
      fail=1
      ;;
  esac

  # The launch stays refused, which is the property the case statement above must
  # not weaken: exit 1, no phase window, and no launch logged. The session itself
  # is persistent by design and exists from the case at the top of this function,
  # so its presence proves nothing and the phase window is what is checked.
  #
  # Nothing can be reserved on this path either, and that half is not asserted
  # here: the stub answers the selector, so the reserving code is never entered
  # at all and a case could not tell that from a tick that had reserved and then
  # refused. The strand fixture is the only place in this suite where a claim can
  # be observed, and it drives the real board of a throwaway repository, which a
  # stubbed selection cannot reach.
  if [ "$unavailable_rc" = 1 ] \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ] \
      && ! tmux_window_exists 2>/dev/null; then
    echo "ok: an unreachable board still refuses the launch, and nothing was opened"
  else
    echo "FAIL: the refusal did not hold (exit $unavailable_rc, launches $(grep -c 'launched phase' "$LOG" || true), window $(tmux_window_exists 2>/dev/null && echo open || echo gone))"
    fail=1
  fi

  # One entry per tick, with the newline shown rather than obeyed. Counting the
  # entries that carry the state is what proves the entry was not broken in two,
  # and comparing the entry with the expected one is what proves the two newlines
  # arrived as two visible characters instead of ending the line and starting
  # another.
  unavailable_entries="$(grep -c 'COORDINATION_UNAVAILABLE (' "$LOG" || true)"
  unavailable_seen="$(grep -m1 'COORDINATION_UNAVAILABLE (' "$LOG" | sed 's/^[^ ]* //' || true)"
  if [ "$unavailable_entries" = 1 ] && [ "$unavailable_seen" = "$unavailable_entry" ]; then
    echo "ok: a multi-line reason is one log entry, with its newlines shown as \\n"
  else
    echo "FAIL: the refusal took $unavailable_entries entries and read '$unavailable_seen'"; fail=1
  fi

  # Once per outage, not once per tick, and once again after the line recovers.
  # The marker directory is the compare-and-swap and the re-arm is what clears
  # it, so this counts the notification the stubbed notifier logged rather than
  # sending anything: DISABLE_NOTIFY and DISABLE_DISCORD are set for the whole of
  # this function, which is how every other case here keeps the channel quiet.
  outage_pattern='notify (disabled): The coordination board cannot be reached'
  # The marker is cleared again first, because the tick above left one and this
  # case has to watch an outage begin rather than continue an earlier one. The
  # first-seen time goes with it, so the outage below is not read as a stale one.
  rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  outage_before="$(grep -c "$outage_pattern" "$LOG" || true)"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_after_two="$(grep -c "$outage_pattern" "$LOG" || true)"
  # A tick the board can answer means the outage is over, so the marker goes and
  # the next outage speaks for itself. A claim on this host's own phase with no
  # launch path is a tick that answers, and it launches nothing.
  DRIVER_STUB_COORDINATION=nopath drive --self-test >/dev/null 2>&1 || true
  wait_phase_window_gone
  if [ -d "$RUN_DIR/coordination-outage" ]; then outage_marker=kept; else outage_marker=cleared; fi
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_end="$(grep -c "$outage_pattern" "$LOG" || true)"
  # The same outage again, but one nobody watched end. The first-seen time beside
  # the marker is rewritten twice: once recent, so the outage is treated as the
  # current one and stays silent, and once far older than any plausible limit, so
  # it cannot plausibly still be current and the next tick has to speak about it.
  # Without the older case the marker outlives its outage and every outage after
  # it is silent for good; without the recent one, a driver that re-announced on
  # every tick would pass.
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  printf '%s' "$(( $(date +%s) - 60 ))" \
    >"$RUN_DIR/coordination-outage-first-seen"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_recent="$(grep -c "$outage_pattern" "$LOG" || true)"
  printf '%s' "$(( $(date +%s) - 400000 ))" \
    >"$RUN_DIR/coordination-outage-first-seen"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_stale="$(grep -c "$outage_pattern" "$LOG" || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$outage_after_two" -eq "$((outage_before + 1))" ] \
      && [ "$outage_marker" = cleared ] \
      && [ "$outage_end" -eq "$((outage_before + 2))" ] \
      && [ "$outage_recent" -eq "$((outage_before + 2))" ] \
      && [ "$outage_stale" -eq "$((outage_before + 3))" ]; then
    echo "ok: two ticks of one outage notify once, a tick that answers re-arms, the next outage notifies again, and only a marker old enough to have outlived its outage speaks again"
  else
    echo "FAIL: once-per-outage did not hold (notices $outage_before -> $outage_after_two, marker $outage_marker, ended $outage_end, recent $outage_recent, stale $outage_stale)"
    fail=1
  fi

  # A state this case statement has no case for, carrying a reason. The catch-all
  # is where a reason matters most, because it exists for a state nobody
  # anticipated, and it must still refuse.
  unknown_launches="$(grep -c 'launched phase' "$LOG" || true)"
  unknown_rc=0
  DRIVER_STUB_COORDINATION=unknown drive --self-test >/dev/null 2>&1 || unknown_rc=$?
  if grep -q "unexpected coordination result 'SOME_FUTURE_STATE' (the selector reported a state this driver has no case for)" "$LOG" \
      && [ "$unknown_rc" = 1 ] \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$unknown_launches" ]; then
    echo "ok: an unexpected state names its reason and still refuses"
  else
    echo "FAIL: an unexpected state did not name its reason, or did not refuse (exit $unknown_rc)"; fail=1
  fi

  # The same catch-all, with the state name carrying a line feed instead of a
  # reason that does. The state name is free text in the selector's answer just as
  # the reason is, and it reaches the same log line, so it is flattened and capped
  # the same way. The comparison is by whole entry rather than by a substring,
  # because a substring would also match a log that had been broken in two and had
  # the rest of the phrase somewhere else in the file.
  split_rc=0
  DRIVER_STUB_COORDINATION=splitstate drive --self-test >/dev/null 2>&1 || split_rc=$?
  split_seen="$(grep -c "unexpected coordination result 'SOME_FUTURE\\\\nSTATE' (the selector reported a state this driver has no case for); refusing to launch" "$LOG" || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$split_seen" = 1 ] && [ "$split_rc" = 1 ]; then
    echo "ok: a state name carrying a line feed is shown as \\n and stays one log entry"
  else
    echo "FAIL: a state name carrying a line feed reached the log line as it arrived (exit $split_rc, matching lines $split_seen)"
    fail=1
  fi

  # The unavailable state with no reason at all. The line has to read the way it
  # read before the state had a case of its own, with no empty brackets in it,
  # because the whole of the change here is adding a reason and a state that has
  # none is the case where adding one must not show.
  silent_rc=0
  DRIVER_STUB_COORDINATION=silent drive --self-test >/dev/null 2>&1 || silent_rc=$?
  silent_line="$(grep -m1 'COORDINATION_UNAVAILABLE; refusing to launch' "$LOG" | sed 's/^[^ ]* //' || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$silent_rc" = 1 ] \
      && [ "$silent_line" = "COORDINATION_UNAVAILABLE; refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)" ]; then
    echo "ok: an unavailable board that gave no reason is named without empty brackets"
  else
    echo "FAIL: an unavailable board with no reason did not read cleanly (exit $silent_rc, line '$silent_line')"
    fail=1
  fi

  # The dry-run half of the same case statement. It dispatches on a different
  # variable and answers 0 because a dry run reports rather than refuses, so it
  # is its own code and needs its own case: an operator who runs a dry run to find
  # out why nothing is starting is asking the same question. DRY_RUN is set here
  # because that is all the --dry-run entry point does.
  #
  # The answer comes from the selector itself rather than from the stub, because
  # the stub is read on the tick path only. A private coordination checkout that
  # is not there is the selector's own way of saying the board cannot be reached,
  # and the store refuses on the folder before it fetches anything, so no board
  # is contacted.
  #
  # The selector's flag array is saved and restored by a copy that expands it in
  # a form every bash accepts, so a host with no work source configured can run
  # the selector at all. Expanding an empty array plainly is an unbound-variable
  # error under `set -u` on bash older than 4.4, which is every Mac, and that
  # aborted the whole run here.
  #
  # A dry run prints to stdout, so its output is kept in a file rather than
  # captured in a substitution. That is no longer a workaround: drive releases
  # the portable lock before it returns, so calling it inside a substitution no
  # longer leaves the lock held for the rest of the process. The file is kept
  # because a dry run's stdout is its result, and this case reads the result
  # twice.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source roadmap)
  load_board_override "$tmp/absent-board-checkout"
  # The tick's own answer is planted first, so the case can tell a dry run that
  # left it alone from one that wrote over it. It is the file an outage
  # notification points the operator at, and a dry run answers a different
  # question with a different reason, so replacing it would replace the evidence.
  printf '%s\n' '{"state":"TICK_ANSWER_PLANTED"}' >"$RUN_DIR/coordination-selection.json"
  rm -f "$RUN_DIR/coordination-selection.dry-run.json"
  DRY_RUN=1
  drive >"$RUN_DIR/dry-unavailable.out" 2>/dev/null || true
  DRY_RUN=0
  load_fixture_config "$selftest_config"
  dry_out="$(cat "$RUN_DIR/dry-unavailable.out" 2>/dev/null || true)"
  if printf '%s' "$dry_out" | grep -q 'dry-run: COORDINATION_UNAVAILABLE (configured board checkout is unavailable: '; then
    echo "ok: a dry run names the reason the board could not be reached"
  else
    echo "FAIL: a dry run did not name the reason (last line: $(printf '%s' "$dry_out" | tail -1))"; fail=1
  fi
  if [ -f "$RUN_DIR/coordination-selection.dry-run.json" ] \
      && printf '%s' "$dry_out" | grep -qF "coordination-selection.dry-run.json"; then
    echo "ok: a dry run keeps its whole answer on disk under a name of its own, and says so"
  else
    echo "FAIL: a dry run named no file of its own, or named one it never wrote"; fail=1
  fi
  if grep -q 'TICK_ANSWER_PLANTED' "$RUN_DIR/coordination-selection.json"; then
    echo "ok: a dry run does not overwrite the answer the outage notification points at"
  else
    echo "FAIL: a dry run overwrote the tick's own coordination-selection.json"; fail=1
  fi

  # A reason longer than the cap is cut, and the cut says how much was dropped,
  # because a bare cut reads as a whole reason when it is half of one. The
  # expected entry is built from the answer the selector really gave, read back
  # out of the file it wrote, so the case pins the cap at 500 and the dropped
  # count without pinning anything about this machine's temporary folder. The
  # path is long rather than the reason, because a path is what a machine can
  # make long by accident, and it carries no character a log entry has to flatten.
  # It is built from several folders rather than one long name because a single
  # name has a length limit of its own, and a name past it is answered with an
  # error the selector does not catch, so the case would measure a crash instead
  # of a refusal.
  long_private="$tmp"
  long_segment=0
  while [ "$long_segment" -lt 5 ]; do
    long_private="$long_private/$(printf 'selftest-long-reason-segment-%090d' "$long_segment")"
    long_segment=$((long_segment + 1))
  done
  load_board_override "$long_private"
  rm -f "$RUN_DIR/coordination-selection.dry-run.json"
  DRY_RUN=1
  drive >"$RUN_DIR/dry-long-reason.out" 2>/dev/null || true
  DRY_RUN=0
  load_fixture_config "$selftest_config"
  long_out="$(cat "$RUN_DIR/dry-long-reason.out" 2>/dev/null || true)"
  full_reason=""
  if [ -f "$RUN_DIR/coordination-selection.dry-run.json" ]; then
    full_reason="$(selection_value error <"$RUN_DIR/coordination-selection.dry-run.json" 2>/dev/null || true)"
  fi
  if [ "${#full_reason}" -gt 500 ]; then
    long_entry="dry-run: COORDINATION_UNAVAILABLE (${full_reason:0:500}...[+$(( ${#full_reason} - 500 )) bytes truncated]); no launch"
    if printf '%s' "$long_out" | grep -qF "$long_entry"; then
      echo "ok: a reason over 500 bytes is cut at 500 and says how many were dropped"
    else
      echo "FAIL: a reason of ${#full_reason} bytes was not cut at 500 with the dropped count (last line: $(printf '%s' "$long_out" | tail -1 | cut -c1-90))"
      fail=1
    fi
  else
    echo "FAIL: the long-reason case never produced a reason over 500 bytes (got ${#full_reason}; last line: $(printf '%s' "$long_out" | tail -1 | cut -c1-90))"
    fail=1
  fi

  # Two more runs of characters, placed so that the byte the cap cuts at falls
  # inside them rather than between them. The case above cuts a run of ASCII at a
  # boundary, so it cannot tell a cut that repairs itself from one that splits a
  # character, and a reason that arrives from git can carry an accent, a branch
  # name written in another script, or a line feed.
  #
  # A path is the only free text that reaches a real selector's reason here, and
  # the reason begins with a fixed sentence followed by a path the selector
  # resolves, so where byte 500 lands depends on this machine's temporary folder
  # rather than on anything the driver chose. It is therefore measured from the
  # same resolution the selector performs, and the expected entry is computed by a
  # separate program reading the answer the selector really wrote, so the case
  # cannot agree with a wrong cut by using the code under test to check itself.
  cut_base="$("$PYTHON" - "$tmp/cut-probe" <<'PY' 2>/dev/null || echo 0
import pathlib, sys
# The sentence the store puts in front of the path it could not use, and the
# resolved path plus the slash that starts the first folder name.
sentence = "configured board checkout is unavailable: "
print(len(sentence) + len(str(pathlib.Path(sys.argv[1]).resolve())) + 1)
PY
)"
  cut_entry_ok=yes
  cut_multibyte_ok=no
  cut_escape_ok=no
  cut_multibyte_detail=none
  cut_escape_detail=none
  cut_probe=none
  for cut_width in 3 2; do
    # Width 3 is a character of three bytes and width 2 is a line feed, which the
    # flatten above turns into a backslash and an n. Both are the same question,
    # so both are asked the same way: shift the run by as many ASCII characters as
    # it takes for the cut to land where this shape needs it, rather than hoping
    # a folder name of the wrong length did it.
    if [ "$cut_width" = 3 ]; then
      cut_char='世'
      cut_per=80
    else
      cut_char='
'
      cut_per=120
    fi
    # Every folder carries 240 bytes of the reason once flattened, and a folder
    # name has a length limit of its own, which is why the run is split across
    # folders rather than put in one long name.
    cut_stride=241
    cut_seg=$(( (499 - cut_base) / cut_stride ))
    [ "$cut_seg" -gt 0 ] || cut_seg=0
    cut_in=$(( 499 - cut_base - cut_seg * cut_stride ))
    while [ "$cut_in" -lt "$cut_width" ] && [ "$cut_seg" -gt 0 ]; do
      cut_seg=$(( cut_seg - 1 ))
      cut_in=$(( cut_in + cut_stride ))
    done
    cut_pad=$(( cut_in % cut_width ))
    cut_pad_name=""
    cut_index=0
    while [ "$cut_index" -lt "$cut_pad" ]; do
      cut_pad_name="p$cut_pad_name"
      cut_index=$(( cut_index + 1 ))
    done
    cut_total=1
    while [ "$(( cut_base + cut_total * cut_stride ))" -lt 503 ]; do
      cut_total=$(( cut_total + 1 ))
    done
    [ "$cut_total" -gt "$(( cut_seg + 1 ))" ] || cut_total=$(( cut_seg + 1 ))
    cut_private="$tmp/cut-probe"
    cut_name=""
    cut_index=0
    while [ "$cut_index" -lt "$cut_per" ]; do
      cut_name="$cut_name$cut_char"
      cut_index=$(( cut_index + 1 ))
    done
    cut_index=0
    while [ "$cut_index" -lt "$cut_total" ]; do
      if [ "$cut_index" = "$cut_seg" ]; then
        cut_private="$cut_private/$cut_pad_name$cut_name"
      else
        cut_private="$cut_private/$cut_name"
      fi
      cut_index=$(( cut_index + 1 ))
    done
    # One ordinary folder after the run, because the selector trims the setting it
    # reads and a last folder made only of line feeds would be trimmed off the end
    # of the answer before the case ever saw it.
    cut_private="$cut_private/cut-run-end"
    load_board_override "$cut_private"
    rm -f "$RUN_DIR/coordination-selection.dry-run.json"
    DRY_RUN=1
    drive >"$RUN_DIR/dry-cut-reason.out" 2>/dev/null || true
    DRY_RUN=0
    load_fixture_config "$selftest_config"
    cut_probe="$("$PYTHON" - "$RUN_DIR/coordination-selection.dry-run.json" \
        "$RUN_DIR/coordination-selection.dry-run.json" <<'PY' 2>&1 || true
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"unreadable ({exc})")
    raise SystemExit(1)
reason = doc.get("error", "")
flat = reason.replace("\r", "").replace("\n", "\\n")
raw = flat.encode("utf-8")
cut = raw[:500]
while cut:
    last = cut[-1]
    if 0x80 <= last <= 0xBF or last >= 0xC0:
        if last < 0xC0:
            cut = cut[:-1]
            continue
        cut = cut[:-1]
        break
    break
try:
    kept = cut.decode("utf-8")
    whole = "yes"
except UnicodeDecodeError:
    kept = cut.decode("utf-8", "replace")
    whole = "no"
edge = raw[499] if len(raw) > 499 else -1
print("line=%s" % (
    "dry-run: COORDINATION_UNAVAILABLE (%s...[+%d bytes truncated]); no launch (see %s)"
    % (kept, len(raw) - len(cut), sys.argv[2])))
print("whole=%s" % whole)
print("edge=%s" % ("lead" if edge >= 0xC0 else
                  "backslash" if raw[499:500] == b"\\" else
                  "continuation" if edge >= 0x80 else "ascii"))
PY
)"
    cut_line="$(printf '%s\n' "$cut_probe" | sed -n 's/^line=//p' || true)"
    cut_whole="$(printf '%s\n' "$cut_probe" | sed -n 's/^whole=//p' || true)"
    cut_edge="$(printf '%s\n' "$cut_probe" | sed -n 's/^edge=//p' || true)"
    if ! printf '%s' "$(cat "$RUN_DIR/dry-cut-reason.out" 2>/dev/null || true)" | grep -qF "$cut_line"; then
      cut_entry_ok=no
    fi
    # One verdict per shape, and each shape's own test decides only its own: a
    # shared else would let the second shape's answer overwrite the first's.
    case "$cut_width" in
      3)
        if [ "$cut_whole" = yes ] && [ "$cut_edge" = lead ]; then
          cut_multibyte_ok=yes
        fi
        cut_multibyte_detail="edge ${cut_edge:-none}, whole ${cut_whole:-none}"
        ;;
      2)
        if [ "$cut_edge" = backslash ]; then
          cut_escape_ok=yes
        fi
        cut_escape_detail="edge ${cut_edge:-none}"
        ;;
    esac
  done

  load_fixture_config "$selftest_config"
  if [ "$cut_multibyte_ok" = yes ] && [ "$cut_escape_ok" = yes ] \
      && [ "$cut_entry_ok" = yes ]; then
    echo "ok: a cut inside a multi-byte character keeps the whole character and the line still decodes, and a cut between the two characters of a flattened newline is pinned"
  else
    echo "FAIL: a cut inside a character was not repaired (multibyte $cut_multibyte_ok: $cut_multibyte_detail; escape $cut_escape_ok: $cut_escape_detail; entry $cut_entry_ok)"
    echo "      probe: $(printf '%s' "$cut_probe" | tr '\n' '|' | cut -c1-200)"
    echo "      line the driver wrote: $(tail -c 200 "$RUN_DIR/dry-cut-reason.out" 2>/dev/null | tr '\n' '|')"
    fail=1
  fi
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # Own-claim recovery. This path had no coverage at all, which is how a
  # `set -u` abort on an unset reservation_id shipped unnoticed: a plain
  # --self-test skips coordination entirely. Three properties matter, so three
  # cases. The stub replays the selection and the takeover; the fence handling
  # and the launch itself are the real code.
  rm -f "$HALT"
  old_stub_sleep="$DRIVER_STUB_SLEEP"
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=2; export DRIVER_STUB_RC DRIVER_STUB_SLEEP
  RECOVER_OWN_CLAIM=0
  export RECOVER_OWN_CLAIM

  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=fence drive --self-test || true
  if grep -q "WAIT_FOR_CLAIM for phase 100010" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: recovery off leaves this host's own claim alone"
  else
    echo "FAIL: recovery=0 did not leave the claim alone"; fail=1
  fi

  RECOVER_OWN_CLAIM=1
  DRIVER_STUB_COORDINATION=fence drive --self-test || true
  sleep 0.3
  if grep -q "on rsv-recovered-stub generation 8" "$LOG"; then
    echo "ok: recovery launches and hands the child the takeover's new fence"
  else
    echo "FAIL: recovery did not carry the new fence from the takeover"; fail=1
  fi
  wait_phase_window_gone
  sleep 0.3

  # The launch path on the recovery path comes from the work source's own result,
  # not from the driver looking for the file. The driver used to glob the roadmap
  # folder here, which put roadmap knowledge back into the driver for no reason:
  # an issue source's recovery would have searched a roadmap for a file named
  # after an issue. The stub names a file the driver never opens, so if the driver
  # still searched, it would find the real phase file instead and print that.
  if grep -q "recovered phase 100010 (.*phase-100010-stub-fixture\.md)" "$LOG"; then
    echo "ok: the recovered launch path comes from the work source, not from a glob"
  else
    echo "FAIL: the recovered launch path did not come from the work source's result"; fail=1
  fi

  # A selection that names a claim but carries no launch path must stop, not fall
  # back to a guess. A guessed path opens a window that closes itself and reads as
  # a phase that ran and died.
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=nopath drive --self-test || true
  sleep 0.3
  if grep -q "returned no launch path from the work source" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: a claim with no launch path from the source refuses to launch"
  else
    echo "FAIL: a claim with no launch path was allowed to launch"; fail=1
  fi
  wait_phase_window_gone
  sleep 0.3

  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=nofence drive --self-test || true
  if grep -q "returned no new fence" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: a takeover that returns no fence refuses to launch"
  else
    echo "FAIL: a fence-less takeover was allowed to launch"; fail=1
  fi

  unset DRIVER_STUB_COORDINATION
  DRIVER_STUB_SLEEP="$old_stub_sleep"; export DRIVER_STUB_SLEEP
  RECOVER_OWN_CLAIM=0
  rm -f "$HALT"

  # The two work-source cases run here, with the coordination stub unset, so the
  # real selector is reached. Under the stub the selection is a canned string
  # and the selector never runs, so a case placed earlier would pass without
  # testing anything.
  #
  # A source name the selector does not know stops the tick rather than falling
  # back to one that would work, because a fallback hides the mistake behind
  # progress.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source no-such-source)
  bogus_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "unknown work source 'no-such-source'" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$bogus_launches" ]; then
    echo "ok: an unknown work source stops the tick with no fallback"
  else
    echo "FAIL: an unknown work source did not stop the tick"; fail=1
  fi

  # The reason has to reach all three of its readers, and the one it used to
  # reach by accident is the one an operator looks at first. Under cron the
  # driver owns stderr, so a reason that stopped being written there moved the
  # message out of the cron log and into driver.log with nothing
  # saying so — an operator debugging a selection failure looked in the log they
  # have always looked in and found nothing. So the reason is captured, recorded
  # under a prefix in driver.log, and still written to stderr.
  #
  # The file and the log are covered by the cases below; this one covers the
  # stderr, which is the behaviour a document claims is unchanged and the one
  # nothing else here can see.
  #
  # The flags are set and restored rather than prefixed onto the call, because a
  # variable assignment in front of a shell function is not reliably scoped to it.
  WORK_SOURCE_FLAGS=(--source no-such-source)
  bogus_stderr="$(selector_run 2>&1 >/dev/null || true)"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
  if printf '%s' "$bogus_stderr" | grep -q "unknown work source 'no-such-source'"; then
    echo "ok: a selector refusal still reaches the driver's own stderr"
  else
    echo "FAIL: a selector refusal no longer reaches stderr, so the cron log loses it (stderr=$bogus_stderr)"; fail=1
  fi

  # A roadmap folder that is not there is a settings mistake, not a queue that
  # has run dry. The two look identical from the selector's exit code, so the
  # refusal is proved here and the folder is named.
  old_roadmap_dir="$ROADMAP_DIR"; old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  missing_root="$(mktemp -d)"
  ROADMAP_DIR="$missing_root/absent"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  missing_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "configured roadmap folder $missing_root/absent is missing or unreadable" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$missing_launches" ]; then
    echo "ok: a missing roadmap folder stops the tick and names the folder"
  else
    echo "FAIL: a missing roadmap folder did not stop the tick"; fail=1
  fi
  rm -rf "$missing_root"
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # A configured roadmap folder outside this checkout can be read but its
  # phases cannot be launched, because the launch is opened on the repository
  # root with a repository-relative phase file. Prove the refusal.
  #
  # This runs through the --self-test branch, which asks the selector without
  # --server and so never touches the shared board. It is therefore the right
  # place to check the refusal and the wrong place to check that nothing was
  # claimed: the strand fixture below is the case that does that, against a real
  # board in a throwaway repository, because claiming is the damage this guard
  # exists to prevent.
  #
  # The setting is forced either way rather than only when this machine already
  # has one: an unconfigured host is the case most deployments are in, and a
  # check that only runs on a configured one would go unexecuted exactly where
  # it is cheapest to run.
  old_roadmap_dir="$ROADMAP_DIR"; old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  outside_root="$(mktemp -d)"
  ROADMAP_DIR="$outside_root/outside"
  mkdir -p "$ROADMAP_DIR"
  # A phase file the selector will actually offer. An empty folder would return
  # NO_PHASE and the run would stop one step earlier than the refusal under
  # test, so the case would pass without reaching it.
  printf '# 100010. Outside Fixture\n- [ ] Required approval is obtained.\n' \
    >"$ROADMAP_DIR/phase-100010-outside-fixture.md"
  printf 'Plan ready\n' >"$ROADMAP_DIR/index.md"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  outside_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "outside $REPO, so its phase files cannot be launched" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$outside_launches" ]; then
    echo "ok: a roadmap folder outside the checkout refuses to launch"
  else
    echo "FAIL: a folder outside the checkout was allowed to launch"; fail=1
  fi
  rm -rf "$outside_root"
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # Naming the default source explicitly has to work, not just be refused. Only
  # the refusals were covered before, so plumbing that dropped --source on the
  # working path would have shipped green.
  #
  # Asked of the selector directly rather than through drive, because drive goes
  # on to cut a real branch in the real checkout once a selection succeeds, and a
  # self-test has no business doing that. The end-to-end half — an explicitly
  # named source reaching a real launch — is the strand fixture's case, which
  # runs against a throwaway repository.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source roadmap)
  named_rc=0
  named_out="$(selector_run 2>/dev/null)" || named_rc=$?
  if [ "$named_rc" -eq 0 ] && [ "${named_out#*$'\t'}" != "$named_out" ]; then
    echo "ok: an explicitly named work source selects like the default"
  else
    echo "FAIL: an explicitly named work source did not reach a selection (rc=$named_rc, out=$named_out)"; fail=1
  fi
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # --check is the report an operator reads when a machine will not advance, so
  # a setting it cannot use has to fail the report rather than be printed inside
  # it. "work source: no-such-source" followed by a zero exit reads as a healthy
  # machine with an odd value. Both settings are set, because the report reads the
  # setting itself and the selector run is given the flag built from it.
  check_rc=0
  old_work_source="$WORK_SOURCE"
  WORK_SOURCE=no-such-source
  WORK_SOURCE_FLAGS=(--source no-such-source)
  check >/dev/null 2>&1 || check_rc=$?
  if [ "$check_rc" -ne 0 ]; then
    echo "ok: --check fails on a work source nothing recognises"
  else
    echo "FAIL: --check reported a bogus work source as healthy (exit 0)"; fail=1
  fi
  old_roadmap_dir="$ROADMAP_DIR"
  WORK_SOURCE="$old_work_source"
  WORK_SOURCE_FLAGS=()
  ROADMAP_DIR="$tmp/check-absent-roadmap"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  check_rc=0
  check >/dev/null 2>&1 || check_rc=$?
  if [ "$check_rc" -ne 0 ]; then
    echo "ok: --check fails on a roadmap folder that is missing"
  else
    echo "FAIL: --check reported a missing roadmap folder as healthy (exit 0)"; fail=1
  fi
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
  check_rc=0
  # The report is kept rather than discarded. `--check` is the report an operator
  # reads when a machine will not advance, so a case that fails it has to be able
  # to say which line of the report objected; a bare exit code sends the next
  # person back to the source to find out.
  check >"$RUN_DIR/check-unconfigured.out" 2>&1 || check_rc=$?
  if [ "$check_rc" -eq 0 ]; then
    echo "ok: --check passes on an unconfigured machine"
  else
    echo "FAIL: --check failed on an unconfigured machine (exit $check_rc)"
    # The lines that object, not the whole report: the report is a JSON dump of
    # two self-tests and the objecting line is one word in it. Each pipeline is
    # wrapped in a group with `|| true` because under `pipefail` a grep that
    # finds nothing fails the pipeline, and a bare failing pipeline stops the
    # run. A diagnostic that stops the run it is diagnosing is worse than none.
    { grep -iE 'MISSING|WARNING|REFUS|unreadable|traceback|error' \
        "$RUN_DIR/check-unconfigured.out" 2>/dev/null \
      | sed 's/^/      /' | head -12; } || true
    { grep -B4 '"ok": false' "$RUN_DIR/check-unconfigured.out" 2>/dev/null \
      | sed 's/^/      /' | head -20; } || true
    fail=1
  fi

  # The merge check runs on every selection, before it is acted on, because the
  # selector no longer stops on an open pull request and this is the only place
  # left that would turn a merged pull request into a finished phase. Driven
  # through the coordination stub so the case runs with no board and no network.
  log "self-test: merge check with a merged and an unmerged pull request"
  before_merged="$(grep -c 'phase 100010 is merged' "$LOG" || true)"
  before_waited="$(grep -c 'phase 100011 still waits' "$LOG" || true)"
  before_health="$(grep -c 'merge check: UNAVAILABLE' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=review DRIVER_STUB_MERGED_PHASE=100010 \
    drive --self-test || true
  sleep 0.3
  merged_now="$(grep -c 'phase 100010 is merged' "$LOG" || true)"
  waited_now="$(grep -c 'phase 100011 still waits' "$LOG" || true)"
  health_now="$(grep -c 'merge check: UNAVAILABLE' "$LOG" || true)"
  if [ "$merged_now" -gt "$before_merged" ]; then
    echo "ok: a merged pull request is confirmed published on a tick that selects a phase (merged $before_merged -> $merged_now)"
  else
    echo "FAIL: the merge check never confirmed a merged pull request (merged stayed $before_merged)"; fail=1
  fi
  if [ "$waited_now" -gt "$before_waited" ] && [ "$health_now" -gt "$before_health" ]; then
    echo "ok: an unmerged pull request is reported without blocking the line, and a degraded merge check says so (waited $before_waited -> $waited_now, degraded $before_health -> $health_now)"
  else
    echo "FAIL: an unmerged pull request or a degraded merge check was not reported (waited $waited_now, degraded $health_now)"; fail=1
  fi
  unset DRIVER_STUB_COORDINATION DRIVER_STUB_MERGED_PHASE
  wait_phase_window_gone
  sleep 0.3

  # A selector that stops on an open pull request is a version disagreement
  # between this script and next_phase.py. It used to log and wait; it now
  # halts, which is right in principle but is a behaviour cliff, so it is
  # pinned here rather than left to production.
  log "self-test: selector still reports AWAITING_REVIEW"
  rm -f "$HALT"
  DRIVER_STUB_COORDINATION=awaiting DISABLE_NOTIFY=1 drive --self-test || true
  sleep 0.3
  if grep -q "the selector should skip an open pull request" "$LOG"; then
    echo "ok: a selector that stops on an open pull request is named, not worked around"
  else
    echo "FAIL: the driver did not notice the selector disagreeing with it"; fail=1
  fi
  unset DRIVER_STUB_COORDINATION DISABLE_NOTIFY
  wait_phase_window_gone
  sleep 0.3

  # Orphan: killing the phase window does not signal the pane, so a live
  # recorded PID with no phase window must halt instead of starting a second
  # concurrent run. The session deliberately SURVIVES the kill here, which is
  # what makes this the real test: a session-scoped existence check would see a
  # live session, wrongly conclude work is in flight, and skip.
  rm -f "$HALT"
  # The stub waits for this file rather than sleeping a span the test guesses at.
  # The check needs a recorded PID that is still alive after the phase window is
  # killed, and a stub that finished on its own timer leaves nothing alive to
  # find, which reads as "orphan not detected" on a driver that is working.
  orphan_release="$tmp/orphan-release"
  orphan_ready="$tmp/orphan-ready"
  rm -f "$orphan_release" "$orphan_ready"
  DRIVER_STUB_RC=0
  DRIVER_STUB_WAIT_FILE="$orphan_release"
  DRIVER_STUB_READY_FILE="$orphan_ready"
  export DRIVER_STUB_RC DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  # On the stub, for the same reason as the cases at the top of this function: a
  # tick with no stub needs a real phase to launch, and reaching a launch with no
  # stub puts this machine's real public checkout on that phase's branch. This case
  # needs a live phase with a recorded pid, which is what the stub provides, and it
  # is the second place that did it before the stub was added here.
  DRIVER_STUB_COORDINATION=fence RECOVER_OWN_CLAIM=1
  export DRIVER_STUB_COORDINATION RECOVER_OWN_CLAIM
  drive --self-test || true
  # The stub's own announcement, not the recorded PID, is what tells the test the
  # stub is alive and ignoring the signal that the kill sends. The PID is written
  # when the pane starts, which is before the stub is in that state, so waiting
  # on it would let the kill arrive while the trap still halts and would make
  # this a test of the signal branch wearing the orphan branch's name.
  orphan_pid=""
  orphan_waited=0
  while [ ! -e "$orphan_ready" ] && [ "$orphan_waited" -lt 200 ]; do
    sleep 0.1
    orphan_waited=$((orphan_waited + 1))
  done
  if [ -e "$orphan_ready" ]; then
    orphan_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$orphan_pid" ] && kill -0 "$orphan_pid" 2>/dev/null; then
      echo "ok: a phase recorded live pid $orphan_pid and is waiting past the window kill"
    else
      echo "FAIL: the stub announced itself ready but recorded no live PID"; fail=1
    fi
  else
    echo "FAIL: the stub never announced itself ready, so the orphan case could not run"; fail=1
  fi
  tmux kill-window -t "$SESSION:$TMUX_PHASE_WINDOW" 2>/dev/null || true
  # Let the kill be observed before asking for the next tick.
  sleep 0.3
  tmux_session_exists \
    && echo "ok: session outlives a killed phase window (orphan precondition)" \
    || echo "note: session gone; orphan test ran without the persistent-session case"
  drive --self-test || true
  grep -q "orphaned run detected" "$LOG" \
    && echo "ok: orphan detected" || { echo "FAIL: orphan not detected"; fail=1; }
  [ -f "$HALT" ] \
    && echo "ok: orphan halts the line" || { echo "FAIL: orphan did not halt"; fail=1; }
  : >"$orphan_release"
  unset DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  unset DRIVER_STUB_COORDINATION
  RECOVER_OWN_CLAIM=0; export RECOVER_OWN_CLAIM
  DRIVER_STUB_SLEEP="$old_stub_sleep"; export DRIVER_STUB_SLEEP
  sleep 0.3
  [ -f "$PIDFILE" ] && kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
  [ -n "$orphan_pid" ] && kill -9 "$orphan_pid" 2>/dev/null || true
  rm -f "$orphan_release" "$orphan_ready"

  # A session outlives every phase, so it is created once and reused forever.
  # That means a settings change, or a hand-made session, would otherwise keep
  # whatever size it was born with. Assert the reconcile path fixes that.
  tmux resize-window -t "$SESSION:$TMUX_IDLE_WINDOW" -x 100 -y 30 2>/dev/null || true
  idle_geom_stale="$(tmux display-message -p -t "$SESSION:$TMUX_IDLE_WINDOW" \
    '#{window_width}x#{window_height}' 2>/dev/null || true)"
  tmux_session_ensure
  idle_geom="$(tmux display-message -p -t "$SESSION:$TMUX_IDLE_WINDOW" \
    '#{window_width}x#{window_height}' 2>/dev/null || true)"
  if [ "$idle_geom" = "${TMUX_WIDTH}x${TMUX_HEIGHT}" ]; then
    echo "ok: idle window reconciled ${idle_geom_stale} -> ${idle_geom}"
  else
    echo "FAIL: idle window stuck at ${idle_geom:-gone}, want ${TMUX_WIDTH}x${TMUX_HEIGHT}"; fail=1
  fi

  # The status bar must stay high-contrast: the tmux default is dim grey on
  # green, which is unreadable on a display across a room. Also asserts the row
  # count was NOT touched, because changing it would resize the window and
  # reach a running agent.
  case "$(tmux show-option -t "$SESSION" -v status-style 2>/dev/null)" in
    *colour15*) echo "ok: status bar is high contrast" ;;
    *) echo "FAIL: status-style not set, got '$(tmux show-option -t "$SESSION" -v status-style 2>/dev/null)'"; fail=1 ;;
  esac
  [ "$(tmux show-options -t "$SESSION" -v status 2>/dev/null)" = "" ] \
    && echo "ok: status bar row count untouched (no resize of a running phase)" \
    || { echo "FAIL: status row count was changed; that resizes the window"; fail=1; }

  # The stage-title hook is a GATING pre_step hook: a nonzero exit stops the
  # run. These assertions exist to fail loudly if that ever changes.
  local hook_config hook_steps hook_last
  hook_config="$("$PYTHON" - "$PIPELINE" "$REPO" <<'PY'
import sys, yaml
from pathlib import Path
pipeline = yaml.safe_load(Path(sys.argv[1]).read_text())
hook = pipeline.get("hooks", {}).get("pre_step", [])
if isinstance(hook, list) and len(hook) == 2 and hook[0] == "bash":
    path = Path(hook[1])
    print(path if path.is_absolute() else Path(sys.argv[2]) / path)
    for step in pipeline.get("steps", {}):
        print(step)
PY
  )"
  title_hook="$(printf '%s\n' "$hook_config" | head -1)"
  hook_steps="$(printf '%s\n' "$hook_config" | tail -n +2)"
  hook_last="$(printf '%s\n' "$hook_steps" | tail -1)"
  if [ -f "$title_hook" ]; then
    hook_sess="ar-hook-selftest-$$"
    tmux new-session -d -s "$hook_sess" -x 80 -y 24 -n phase 'sleep 30' 2>/dev/null || true
    hook_rc=0
    while IFS= read -r hook_step; do
      GLIDE_STEP="$hook_step" CLIO_TMUX_SESSION="$hook_sess" \
        bash "$title_hook" >/dev/null 2>&1 || hook_rc=$?
    done <<<"$hook_steps"
    [ "$hook_rc" -eq 0 ] \
      && echo "ok: stage hook exits 0 for configured steps" \
      || { echo "FAIL: stage hook exited $hook_rc"; fail=1; }
    hook_title="$(tmux show-option -t "$hook_sess" -v set-titles-string 2>/dev/null || true)"
    case "$hook_title" in
      *"$hook_last"*) echo "ok: stage hook set the last title ($hook_title)" ;;
      *) echo "FAIL: stage hook title unexpected: '$hook_title'"; fail=1 ;;
    esac
    hook_win="$(tmux list-windows -t "$hook_sess" -F '#{window_name}' 2>/dev/null | tail -1)"
    case "$hook_win" in
      *"$hook_last"*) echo "ok: stage hook renamed the phase window ($hook_win)" ;;
      *) echo "FAIL: stage hook window name unexpected: '$hook_win'"; fail=1 ;;
    esac
    # The rename must not make the window unfindable: the driver looks it up by
    # ID, so a display label like "phase adversary" cannot break a later
    # interrupt, pipe-pane, or geometry read.
    hook_wid="$(tmux list-windows -t "$hook_sess" -F '#{window_id} #{window_name}' 2>/dev/null \
      | awk '$2 ~ /^phase/ { print $1 }')"
    [ -n "$hook_wid" ] \
      && echo "ok: renamed phase window still resolvable by id ($hook_wid)" \
      || { echo "FAIL: renamed phase window is no longer resolvable"; fail=1; }
    # Missing session and missing step must both be silent and harmless.
    GLIDE_STEP="$hook_last" CLIO_TMUX_SESSION="ar-no-such-session-$$" \
      bash "$title_hook" >/dev/null 2>&1 \
      && echo "ok: stage hook tolerates a missing session" \
      || { echo "FAIL: stage hook failed on a missing session"; fail=1; }
    GLIDE_STEP="" CLIO_TMUX_SESSION="$hook_sess" \
      bash "$title_hook" >/dev/null 2>&1 \
      && echo "ok: stage hook tolerates an empty step" \
      || { echo "FAIL: stage hook failed on an empty step"; fail=1; }
    tmux kill-session -t "$hook_sess" 2>/dev/null || true
  elif [ -n "$title_hook" ]; then
    echo "FAIL: stage hook script missing at $title_hook"; fail=1
  else
    echo "ok: no configured stage-title hook"
  fi

  # Stranding case. A phase leaves the index dirty for its whole life, so a
  # remote that moves during the phase must not stop the next tick from
  # reaching the launch. This is the case that stranded phase 100800: the
  # refusal happened before the claim-recovery path, so neither the approval
  # nor the halt marker could unblock the line.
  #
  # The fixture is real: two bare remotes, two checkouts, a staged and an
  # unstaged edit in each, and a clean commit pushed to each remote that
  # touches a different path. Only the agent harness is stubbed.
  strand_root="$tmp/strand-root"
  strand_run="$tmp/strand-run"
  strand_ok=1
strand_diagnose() {
  echo "--- strand driver.log ---"
  sed 's/^/    /' "$LOG" 2>/dev/null || echo "    (no log)"
  if [ -f "$RUN_DIR/coordination-sync.json" ]; then
    echo "--- strand coordination-sync.json ---"
    "$PYTHON" -c 'import json,sys
for r in json.load(open(sys.argv[1])).get("repos", []):
    print("   ", r.get("repo"), r.get("relation"), r.get("ok"),
          r.get("blockers"), r.get("overlapping"), r.get("dirty"), r.get("staged"))
' "$RUN_DIR/coordination-sync.json" 2>/dev/null || \
      sed 's/^/    /' "$RUN_DIR/coordination-sync.json"
  fi
}
STRAND_BOARD=""
strand_board_read() {
  # Read the board file and branch named by this fixture's config.
  local state_rel="${BOARD_FILE#"${BOARD_REPO%/}"/}"
  STRAND_BOARD="$(git -C "$BOARD_REPO" \
    show "$BOARD_REMOTE/$PHASE_COORDINATION_BRANCH:$state_rel" \
    2>/dev/null || true)"
}
# Run one tick with the settings the caller has already put in place, and report
# whether it refused without opening a window or writing to the board.
#
#   $1  text the driver's log must carry, naming the refusal
#
# This is the harness a post-reservation refusal has to be checked with, and the
# strand fixture is the only place it can be used: the cases in the main
# self-test run through the --self-test branch, which asks the selector without
# --server and therefore never claims anything at all. A check on that branch
# cannot see the damage, because the damage is a claim.
#
# Both of the cheap signals are wrong on their own. The log proves the driver
# said no, and the tick's exit code is not consulted at all because a refusal is
# supposed to fail the tick. What has to be checked is the board, because a
# claim taken by a tick that then refuses is the one failure the line cannot
# clear by itself: every later tick reads that claim back as WAIT_FOR_CLAIM,
# logs it, and exits 0, so the line stops silently and only an operator takeover
# restarts it. The whole file is compared rather than one phase, because a claim
# under any name at all is a claim.
#
# 0 refused as asked and the board came back byte-for-byte, 1 it launched or
# logged something else, 2 it wrote to the board, 3 the board could not be read,
# in which case the case proves nothing and the caller must report a failure
# rather than pass it.
strand_expect_refused() {
  local expect="$1" before after launches
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  strand_board_read
  before="$STRAND_BOARD"
  # The fixture's own claim on 100010 is the evidence this board is really being
  # read. An unreadable board answers "unchanged" for exactly the same reason an
  # unclaimed phase would, so without this the case passes on a broken read.
  printf '%s' "$before" | grep -q '"100010"' || return 3
  drive >/dev/null 2>&1 || true
  grep -q "$expect" "$LOG" || return 1
  [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ] || return 1
  strand_board_read
  after="$STRAND_BOARD"
  [ -n "$after" ] || return 3
  [ "$after" = "$before" ] || return 2
  return 0
}
  # The stranding case needs its own root rather than the suite's, because it
  # needs pre-existing state the suite's root must not have: a staged and an
  # unstaged edit in each checkout, and a clean commit already pushed by another
  # developer. Both fixtures come from the same two helpers, so a change to the
  # builder reaches both.
  if selftest_fixture_root "$strand_root" "$tmp/strand" \
      && selftest_fixture_stage_work "$strand_root" "$tmp/strand"; then
    old_repo="$REPO"; old_board_dir="$BOARD_DIR"; old_wf="$WF"
    old_run_dir="$RUN_DIR"; old_lock="$LOCK"; old_halt="$HALT"; old_stall="$STALL"
    old_current="$CURRENT"; old_pid="$PIDFILE"; old_notify_broken="$NOTIFY_BROKEN"
    old_log="$LOG"; old_session="$SESSION"; old_argus="$ARGUS"
    old_notifier="$NOTIFIER"; old_pipeline="$PIPELINE"
    old_roadmap_dir="$ROADMAP_DIR"; old_work_source="$WORK_SOURCE"
    old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
    REPO="$strand_root"; BOARD_DIR=board-data
    ARGUS="$strand_root/$BOARD_DIR/argus"
    NOTIFIER="$ARGUS/phase_notifications.py"
    # A configuration file for this sub-fixture, so every tick it launches reads
    # the strand checkout rather than the suite's.
    ARGUS_CONFIG_FILE="$tmp/strand-config.jsonc"
    selftest_write_config "$strand_root" "$ARGUS_CONFIG_FILE" "$BOARD_DIR"
    load_fixture_config "$ARGUS_CONFIG_FILE"
    WORK_SOURCE_FLAGS=(--source roadmap)
    strand_run="$WF/$DRIVER_STATE"
    mkdir -p "$strand_run"
    RUN_DIR="$strand_run"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    SESSION="ar-driver-strand-$$"
    # Only the agent harness is stubbed. Coordination and the sync are the real
    # code, because a stubbed board would skip the sync, which is the code
    # under test here.
    DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=1
    export DRIVER_STUB_RC DRIVER_STUB_SLEEP

    # First, the claim must still be respected when the operator has not asked
    # for a takeover. The sync has to pass to reach the claim at all, so a
    # refusal here would mask the thing being tested.
    drive >/dev/null 2>&1 || true
    if grep -q "WAIT_FOR_CLAIM for phase 100010" "$LOG"; then
      echo "ok: recovery off still waits for this host's own claim"
    else
      echo "FAIL: recovery=0 did not wait for the claim"; strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # Every way this machine can be configured wrongly must be refused above the
    # reservation, and the claim is the damage: a tick that claims and then
    # refuses leaves a live claim on the shared board, every later tick reads it
    # back as WAIT_FOR_CLAIM, logs it, and exits 0, so the line stops silently and
    # only an operator takeover restarts it. That is why the refusal sits above
    # the selector here and not beside the launch.
    #
    # These are the cases that can see it. The ones in the main self-test ask the
    # selector without --server and therefore never claim, so they cannot observe
    # the thing that goes wrong. These drive the real reserving path against a
    # board of their own and read that board afterwards, which is the only way to
    # find out what a refused tick left behind.
    #
    # All three refusals are covered rather than only the outside-folder one,
    # because they are refused in different places: the folder is checked in the
    # driver's shell before the selector runs, and the source name is checked
    # inside the selector. One of the three can move below the reservation
    # without the other two noticing, and a move is exactly what a later edit
    # makes when someone tidies the ordering.
    #
    # The phase each folder offers is one the fixture board knows nothing about,
    # so a claim appears only if this tick takes one. Nothing else in the fixture
    # refers to them.
    strand_outside="$tmp/strand-outside-roadmap"
    strand_unclaimed=100099
    mkdir -p "$strand_outside"
    printf '# %s. Outside Strand Fixture\n- [ ] Required approval is obtained.\n' \
      "$strand_unclaimed" >"$strand_outside/phase-$strand_unclaimed-outside-strand.md"
    printf 'Plan ready\n' >"$strand_outside/index.md"
    ROADMAP_DIR="$strand_outside"
    WORK_SOURCE_FLAGS=(--roadmap "$strand_outside")
    if strand_expect_refused \
        "outside $REPO, so its phase files cannot be launched"; then
      echo "ok: a folder outside the checkout refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: a folder outside the checkout was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: a folder outside the checkout was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # A folder that is not there at all. Same claim to strand, same place to
    # refuse, and it is the one a typo in a settings file produces.
    ROADMAP_DIR="$tmp/strand-absent-roadmap"
    WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
    if strand_expect_refused \
        "configured roadmap folder $tmp/strand-absent-roadmap is missing or unreadable"; then
      echo "ok: a missing roadmap folder refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: a missing roadmap folder was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: a missing roadmap folder was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # A source name nothing recognises. This one is refused by the selector rather
    # than by the driver's shell, so it is the case that would pass if only the
    # shell's own check were trusted: the shell does not check the source at all,
    # and the selector's refusal has to arrive before anything is claimed.
    ROADMAP_DIR=""
    WORK_SOURCE_FLAGS=(--source no-such-source)
    if strand_expect_refused "unknown work source 'no-such-source'"; then
      echo "ok: an unknown work source refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: an unknown work source was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: an unknown work source was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # Back to the fixture's own folder: every case below selects from it.
    ROADMAP_DIR=""; WORK_SOURCE_FLAGS=()

    # Then the takeover: the tick must reach the launch with the phase's work
    # still in the index and both remotes moved.
    before_index="$(git -C "$strand_root/$BOARD_DIR" diff --cached)"
    before_fix="$(cat "$strand_root/$BOARD_DIR/phase.txt")"
    RECOVER_OWN_CLAIM=1; export RECOVER_OWN_CLAIM
    drive >/dev/null 2>&1 || true
    if grep -q "launched phase" "$LOG" \
        && ! grep -q "coordination checkout sync failed" "$LOG"; then
      echo "ok: a dirty index and a moved remote still reach the launch"
    else
      echo "FAIL: a moved remote with a dirty index stopped the launch"; strand_ok=0; fail=1
      strand_diagnose
    fi
    if [ "$(git -C "$strand_root/$BOARD_DIR" diff --cached)" = "$before_index" ] \
        && [ "$(cat "$strand_root/$BOARD_DIR/phase.txt")" = "$before_fix" ]; then
      echo "ok: the review baseline survives the sync untouched"
    else
      echo "FAIL: the sync changed the phase's staged or unstaged work"; strand_ok=0; fail=1
    fi
    wait_phase_window_gone

    # An explicitly named work source must reach a real launch, not only be
    # refused. Naming the source rather than letting it default is the setting an
    # operator sets by hand, and it has to drive the whole path: selection,
    # reservation, branch cut and window. Safe here and nowhere else, because
    # this repository and this board are the fixture's own.
    WORK_SOURCE_FLAGS=(--source roadmap)
    # Counts, not presence. The log is cumulative and an earlier case in this
    # same fixture refused an unknown source on purpose, so a presence test would
    # read that deliberate refusal as this tick failing.
    named_launches="$(grep -c 'launched phase' "$LOG" || true)"
    named_refusals="$(grep -c 'unknown work source' "$LOG" || true)"
    drive >/dev/null 2>&1 || true
    if [ "$(grep -c 'launched phase' "$LOG" || true)" -gt "$named_launches" ] \
        && [ "$(grep -c 'unknown work source' "$LOG" || true)" = "$named_refusals" ]; then
      echo "ok: an explicitly named work source drives a full launch"
    else
      echo "FAIL: an explicitly named work source did not reach a launch"; strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone
    WORK_SOURCE_FLAGS=()

    # A genuine overlap must still stop the line, and must say which path.
    # The phase is on its own branch now, so the incoming commit has to arrive
    # on that branch: remote movement on the integration branch no longer
    # reaches the phase until the branch is merged, which is the point of it.
    strand_branch="feature/strand-fixture"
    if git -C "$strand_root/$BOARD_DIR" rev-parse --verify "$strand_branch" >/dev/null 2>&1; then
      git clone -q -b "$strand_branch" "$tmp/strand/priv.git" \
        "$tmp/strand/other-overlap" 2>/dev/null || true
      if [ -d "$tmp/strand/other-overlap" ]; then
        printf 'three\n' >>"$tmp/strand/other-overlap/b.txt"
        git -C "$tmp/strand/other-overlap" add -A
        git -C "$tmp/strand/other-overlap" commit -q -m "other: touch b.txt on the phase branch"
        git -C "$tmp/strand/other-overlap" push -q origin "$strand_branch"
        rm -rf "$tmp/strand/other-overlap"
        # Make the phase's edit overlap the incoming commit.
        git -C "$strand_root/$BOARD_DIR" add phase.txt
        printf 'phase baseline\nremediator fix\n' >"$strand_root/$BOARD_DIR/b.txt"
        git -C "$strand_root/$BOARD_DIR" add b.txt
        rm -f "$HALT"
        echo "--- overlap precondition ---"
        echo "    on branch: $(git -C "$strand_root/$BOARD_DIR" rev-parse --abbrev-ref HEAD)"
        echo "    incoming: $(git -C "$strand_root/$BOARD_DIR" fetch -q origin && git -C "$strand_root/$BOARD_DIR" diff --name-only --no-renames HEAD "origin/$strand_branch" | tr '\n' ' ')"
        echo "    dirty:    $(git -C "$strand_root/$BOARD_DIR" status --porcelain=v1 --untracked-files=all | tr '\n' '|')"
        log_count_before="$(grep -c 'launched phase' "$LOG" || true)"
        drive >/dev/null 2>&1 || true
        if grep -q "coordination checkout sync failed" "$LOG" \
            && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$log_count_before" ]; then
          echo "ok: a genuine overlap still refuses the launch"
        else
          echo "FAIL: an overlapping path did not refuse the launch"; strand_ok=0; fail=1
          strand_diagnose
        fi
        if grep -q '"overlapping"' "$RUN_DIR/coordination-sync.json" 2>/dev/null; then
          echo "ok: the overlap refusal names the overlapping path"
        else
          echo "FAIL: the overlap refusal did not name a path"; strand_ok=0; fail=1
          strand_diagnose
        fi
      fi
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
    RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
    CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
    LOG="$old_log"; SESSION="$old_session"; ARGUS="$old_argus"
    NOTIFIER="$old_notifier"; PIPELINE="$old_pipeline"
    ROADMAP_DIR="$old_roadmap_dir"; WORK_SOURCE="$old_work_source"
    WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
    load_fixture_config "$selftest_config"
  else
    # The builder writes its own reason to stderr, and stderr was not captured
    # here so that it reaches the reader; this line says which stage failed.
    echo "FAIL: could not build the stranding fixture, so no stranding case ran"
    strand_ok=0; fail=1
  fi

  tmux kill-session -t "$SESSION" 2>/dev/null || true
  WF="$selftest_wf"
  # Reached the last check, so a stop from here on is a stop the run chose.
  SELFTEST_REACHED_END=1
  # The trap is deliberately left armed. It is what removes the fixture tree, and
  # it is what runs the tripwire, and it runs on this path as much as on an abort.
  # Nothing it does can fail here: `rm -rf` on an absent path succeeds, and every
  # `tmux kill-session` is guarded. Removing the fixture and disarming the trap
  # from here instead would mean the tripwire never runs on a successful run,
  # which is the only kind of run most of them are.
  echo "driver self-test: $([ "$fail" = 0 ] && echo pass || echo FAIL)"
  return "$fail"
}

# One line per configured value: the shell variable, its value, and whether a
# flag or the file supplied it. Every schema key has a line, and an optional key
# the file omitted says so rather than reading as a healthy default.
check_config_value() {
  local var="$1" value source
  case "$var" in
    PHASE_INCLUDE|PHASE_EXCLUDE) eval "value=\"\${$var[*]-}\"" ;;
    *) value="${!var-}" ;;
  esac
  value="${value//$'\n'/,}"
  eval "source=\"\${ARGUS_SOURCE_$var-}\""
  if [ -z "$source" ]; then
    printf '%s: %s (unset; not in config)\n' "$var" "${value:-<unset>}"
  else
    printf '%s: %s (%s)\n' "$var" "${value:-<empty>}" "$source"
  fi
}

check() {
  local name label cmd bin selector_rc=0 warned=0 var
  for name in "tmux:$CMD_TMUX" "hermes:$CMD_HERMES" "glide:$CMD_GLIDE"; do
    label="${name%%:*}"; cmd="${name#*:}"
    if bin="$(command -v "$cmd" 2>/dev/null)"; then
      echo "$label: $bin (from '$cmd')"
    elif [ -f "$cmd" ] && [ -x "$cmd" ]; then
      echo "$label: $cmd"
    else
      echo "$label: MISSING (from '$cmd')"; [ "$label" != glide ] || warned=1
    fi
  done
  if "$PYTHON" -c 'import yaml' >/dev/null 2>&1; then
    echo "python3: $PYTHON (PyYAML OK)"
  else
    echo "python3: $PYTHON (PyYAML MISSING - runner.py will fail on import)"
  fi
  if command -v flock >/dev/null 2>&1; then
    echo "flock: $(command -v flock)"
  else
    echo "flock: absent (double-start guard falls back to the tmux session check)"
  fi
  if command -v "$CMD_HERMES" >/dev/null 2>&1 \
      && "$CMD_HERMES" -p "$PROFILE" send --list >/dev/null 2>&1; then
    echo "profile '$PROFILE': usable for send"
  elif [ "${NOTIFY_DISABLED:-0}" = 1 ] || [ -z "$PROFILE" ]; then
    echo "profile: notifications off"
  else
    echo "profile '$PROFILE': NOT usable here (need a local Hermes profile with Discord)"
  fi
  # The configuration report: the file it read, then every schema value with the
  # flag-or-file source the module recorded. This is the one place an operator
  # can see which of the two supplied a value, so it is printed before the
  # folder checks that depend on those values.
  echo "config file: ${ARGUS_CONFIG_FILE:-none}"
  echo "configuration precedence: $CONFIG_PRECEDENCE"
  echo "driver home: $ARGUS"
  for var in REPO PIPELINE ROADMAP_DIR PHASE_NUMBER_INPUT PHASE_FILE_INPUT \
             DRIVER PHASE_INCLUDE PHASE_EXCLUDE WF DRIVER_STATE \
             STALE_AFTER_SEC OUTAGE_STALE_SEC QUIET_MINUTES LOG_KEEP_DAYS \
             MERGE_CHECK_MAX RECOVER_OWN_CLAIM PHASE_MACHINE_ID \
             CMD_GLIDE CMD_GIT CMD_TMUX CMD_PYTHON CMD_HERMES CMD_GH \
             SESSION_NAME TMUX_WIDTH TMUX_HEIGHT TMUX_IDLE_WINDOW \
             TMUX_PHASE_WINDOW TMUX_IDLE_SCRIPT TMUX_REFRESH \
             BOARD_REPO BOARD_FILE BOARD_BRANCH BOARD_REMOTE \
             HERMES_PROFILE_NAME DISCORD_CHANNEL NOTIFY_DISABLED \
             DISCORD_FALLBACK_CHANNEL HEARTBEAT_URL; do
    check_config_value "$var"
  done
  # A folder that does not exist is an error, not a note: the tick it belongs to
  # cannot start, so a report that says nothing about it is a report that reads
  # as healthy.
  for required in "$ARGUS/phase-driver.sh" "$ARGUS/gitsync.py" \
                "$PIPELINE"; do
    if [ ! -e "$required" ]; then
      echo "MISSING: $required"
      warned=1
    fi
  done
  echo "work source: ${WORK_SOURCE:-roadmap}"
  # The launch suffix says whether this machine can actually run what it
  # selects: a folder outside this checkout is readable but not launchable.
  if [ -n "$ROADMAP_DIR" ]; then
    if roadmap_dir_readable; then
      echo "roadmap dir: $(roadmap_dir) (configured)"
    else
      echo "roadmap dir: $(roadmap_dir) MISSING OR UNREADABLE, this machine's ticks will refuse to launch"
      warned=1
    fi
    roadmap_dir_is_local \
      || { echo "roadmap dir: WARNING outside this checkout, so its phases cannot be launched here"; warned=1; }
  fi
  # The configured work-source command line, actually run. A source name nothing
  # recognises and a folder that cannot be read both stop the selector with exit
  # 2 and the reason on stderr, and refusing to launch for either is the
  # behaviour this report exists to confirm. Exit 0 (an item to run) and 1
  # (nothing left to run) are both healthy; only the refusal is not.
  #
  # Reporting the refusal is not enough on its own, so a refusal is also the
  # exit code. Printing "work source: no-such-soyrce" and exiting 0 reads as a
  # healthy machine with an odd value, which is how a typo in a settings file
  # reaches a line that then refuses to advance every ten minutes.
  mkdir -p "$RUN_DIR"
  "$PYTHON" "$ARGUS/next_phase.py" "${ARGUS_CONFIG_ARGS[@]}" --repo "$REPO" \
    "${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}" >/dev/null 2>"$RUN_DIR/check-selector.err" || selector_rc=$?
  if [ "$selector_rc" -eq 2 ]; then
    echo "work source command line: REFUSED, this machine's ticks will refuse to launch"
    sed 's/^/  /' "$RUN_DIR/check-selector.err" 2>/dev/null || true
    warned=1
  else
    echo "work source command line: accepted (exit $selector_rc)"
  fi
  echo "heartbeat: ${HEARTBEAT_URL:-unset}"
  echo "stall threshold: ${STALE_AFTER_SEC}s"
  echo "tmux geometry: ${TMUX_WIDTH}x${TMUX_HEIGHT} (pinned with window-size manual; a display attached with -r cannot change it)"
  if tmux_session_exists; then
    echo "tmux session '$SESSION': present (persistent, outlives every phase)"
    tmux_window_exists \
      && echo "  phase window: running, $(tmux_phase_window_geometry)" \
      || echo "  phase window: absent (idle)"
    echo "  title: $("$CMD_TMUX" show-option -t "$SESSION" -v set-titles-string 2>/dev/null || echo '(unset)')"
    echo "  attach read-only: tmux attach -t $SESSION -r"
  else
    echo "tmux session '$SESSION': not created yet (created on the next launch)"
  fi
  "$PYTHON" "$ARGUS/next_phase.py" --self-test || warned=1
  "$PYTHON" "$ARGUS/phase_reservations.py" --self-test || warned=1
  return "$warned"
}

case "${1:-}" in
  --session)
    [ "$#" -eq 3 ] || { echo "usage: phase-driver.sh --session <N> <phase-file>" >&2; exit 2; }
    run_session "$2" "$3"
    ;;
  --check)     check ;;
  --self-test) driver_self_test ;;
  --stop)      stop ;;
  --dry-run)   DRY_RUN=1; drive ;;
  ""|--drive)  drive ;;
  *)
    echo "usage: phase-driver.sh [--dry-run|--check|--self-test|--stop] | --session <N> <phase-file>" >&2
    exit 2
    ;;
esac
