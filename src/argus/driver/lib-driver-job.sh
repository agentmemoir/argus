#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# One job: retry a failed run, and record a failure that outlasts the retries.

# The retries after the first attempt for one failed job. The operator sets the
# count in ARGUS_JOB_RETRIES; anything that is not a whole number falls back to
# the default of 3, so a mistyped value cannot turn retries off.
job_retries() {
  local retries="${ARGUS_JOB_RETRIES:-3}"
  case "$retries" in
    ''|*[!0-9]*) printf '3' ;;
    *) printf '%s' "$retries" ;;
  esac
}

# Run a command and retry a failure with capped exponential backoff and full
# jitter. The default of 3 retries is four attempts: the first try plus three.
# Each wait is a uniform random span in [0, min(cap, base * 2^n)], the full
# jitter schedule in the AWS Architecture Blog's "Exponential Backoff And
# Jitter": jitter spreads the retries so a fleet does not retry in lockstep, and
# the cap stops the wait from growing without bound. Returns the last rc.
job_retry() {   # <command> [args...]
  local retries attempt=1 rc=0 backoff=5 cap=60 ceiling wait
  retries="$(job_retries)"
  while :; do
    "$@" && return 0
    rc=$?
    [ "$attempt" -le "$retries" ] || return "$rc"
    ceiling="$backoff"
    [ "$ceiling" -le "$cap" ] || ceiling="$cap"
    wait="$((RANDOM % (ceiling + 1)))"
    log "job attempt $attempt failed (rc=$rc); retry $attempt of $retries in ${wait}s"
    sleep "$wait"
    backoff=$((backoff * 2))
    attempt=$((attempt + 1))
  done
}

# Run the phase's glide pipeline once for one attempt.
#
# The fresh-archive flag is recomputed from the state the previous attempt left,
# so a retry after a terminal failure starts the run over instead of being
# refused. Reads run_session's locals (number, rel, glide_inputs) through bash's
# dynamic scope.
glide_run_once() {
  local prior_state="" fresh_args=() run_dir map_file driver_map
  run_dir="$(phase_run_dir "$number" "$rel")"
  prior_state="$(terminal_state_of_file "$run_dir/run.json")"
  # run.json is the marker glide writes first, and the --fresh decision below
  # reads it. A failed.json with no run.json beside it is residue from a glide
  # stop before that marker. glide does not archive it, so the new run would
  # keep it and the selector would skip the phase. Clear it before the run starts.
  if [ ! -f "$run_dir/run.json" ]; then
    rm -f "$run_dir/failed.json"
  fi
  if [ "${CLIO_RESUME:-}" != 1 ] && [ -n "$prior_state" ]; then
    fresh_args+=(--fresh)
    log "${work_kind:-phase} $number run is terminal ($prior_state); restarting it with --fresh"
    # The restart archives the run directory. glide keeps an owner-edited
    # harnesses.json across it and fills only the missing fields (plan 0010,
    # C4/B8); the map is owner-edited when it differs from the snapshot glide
    # last wrote. This line is written before glide runs, so it can only
    # claim the detection, never a keep glide has not done yet; no sidecar,
    # or an equal pair, is no evidence of an edit, and no argus code chooses
    # a harness.
    map_file="$run_dir/harnesses.json"
    driver_map="$run_dir/harnesses.driver.json"
    if [ -f "$map_file" ] && [ -f "$driver_map" ] && ! cmp -s "$map_file" "$driver_map"; then
      log "${work_kind:-phase} $number owner-edited $map_file detected before the --fresh restart; glide keeps and fills only the missing fields"
    fi
  fi
  # The pre_run hook refuses a run while this checkout's git hooks are missing or
  # stale, so they are made ready before glide starts. A refusal that remains stops
  # this launch, as a refused pre_run hook does, and the line says why.
  local hooks_out hooks_rc=0
  hooks_out="$("$PYTHON" "$ARGUS/hooks_setup.py" "$RUN_DIR" --repo "$REPO" "${ARGUS_CONFIG_ARGS[@]}" 2>&1)" || hooks_rc=$?
  [ -z "$hooks_out" ] || log "$hooks_out"
  [ "$hooks_rc" -eq 0 ] || return 1
  # Glide and its stages take their temporary folder from the run state, not from
  # the caller's TMPDIR, so a host whose default folder is mounted noexec still
  # starts a stage. The readiness check probes the folder.
  local stage_tmp="$RUN_DIR/stage-tmp"
  mkdir -p "$stage_tmp"
  log "stage temp dir: $stage_tmp (TMPDIR and BUN_TMPDIR for glide)"
  TMPDIR="$stage_tmp" BUN_TMPDIR="$stage_tmp" "$CMD_GLIDE" run --pipeline "$PIPELINE" \
    "${fresh_args[@]+"${fresh_args[@]}"}" "${glide_inputs[@]}"
}

# Record a job that failed after its retries, so the selector skips it and the
# line continues to the next job. The residue is written atomically beside the
# run directory, which is left alone for the evidence a person reads.
failure_residue_write() {   # <number> <rel> <state> <rc> <retries>
  local dir
  dir="$(phase_run_dir "$1" "$2")"
  # A job can fail before glide creates its run directory, so make it here; a
  # residue that cannot be written is logged and never halts the line.
  mkdir -p "$dir" || {
    log "job-failed: could not create $dir to record the failure; continuing"
    return 0
  }
  "$PYTHON" - "$dir/failed.json.tmp.$$" "$1" "$3" "$4" "$5" "$(ts)" \
    "$dir/failed.json" <<'PY'
import json, os, sys


def as_number(value):
    return int(value) if value.isdigit() else value


residue = {
    "version": 1,
    "phase": sys.argv[2],
    "state": sys.argv[3],
    "rc": as_number(sys.argv[4]),
    "retries": as_number(sys.argv[5]),
    "failed_at": sys.argv[6],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(residue, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(sys.argv[1], sys.argv[7])
PY
  [ "$?" -eq 0 ] \
    || log "job-failed: could not record the failure at $dir/failed.json; continuing"
  return 0
}
