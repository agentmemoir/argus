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
  local prior_state="" fresh_args=()
  prior_state="$(terminal_state_of_file "$(phase_run_dir "$number" "$rel")/run.json")"
  if [ "${CLIO_RESUME:-}" != 1 ] && [ -n "$prior_state" ]; then
    fresh_args+=(--fresh)
    log "${work_kind:-phase} $number run is terminal ($prior_state); restarting it with --fresh"
  fi
  "$CMD_GLIDE" run --pipeline "$PIPELINE" \
    "${fresh_args[@]+"${fresh_args[@]}"}" "${glide_inputs[@]}"
}

# Record a job that failed after its retries, so the selector skips it and the
# line continues to the next job. The residue is written atomically beside the
# run directory, which is left alone for the evidence a person reads.
failure_residue_write() {   # <number> <rel> <state> <rc> <retries>
  local dir
  dir="$(phase_run_dir "$1" "$2")"
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
}
