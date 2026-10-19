#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases for the job retry path:
#
# - a job failure is recorded even when its run directory is absent;
# - a terminal run with an owner-edited harness map keeps the edit across the
#   --fresh restart (plan 0010, B8/C4).
#
# A glide job can fail before it writes run.json, so failure_residue_write has to
# create the directory itself. Without that the residue is never written, the
# selector offers the same phase again, and the line loops on the same failure.

# Test double for glide's `run --fresh` on the retry path. It records the flags
# it was handed, archives the old run directory as a real fresh start does, and
# restores the owner-edited map into the new one: that is the keep contract
# plan 0010 (C4) puts on glide. Argus's own half is the --fresh decision and the
# log line naming the kept edit; the new run directory's map is read back from
# this stub. No argus code chooses a harness.
glide_keep_edit_stub() {
  printf '%s\n' "$*" >"$job_stub_args"
  local run_dir archived
  run_dir="$(phase_run_dir "$number" "$rel")"
  archived="$run_dir.prev-$$"
  mv "$run_dir" "$archived"
  mkdir -p "$run_dir"
  cp "$archived/harnesses.json" "$run_dir/harnesses.json"
  printf '{"state":"done"}\n' >"$run_dir/run.json"
}

selftest_case_job_fresh_keeps_edit() {
  # A terminal run whose harness map the owner edited must survive the retry:
  # the --fresh restart archives the run directory, and the new run's
  # harnesses.json still names the edited harness. Argus never chooses a
  # harness; it decides --fresh, logs the kept edit naming the file and the
  # run, and lets glide do the keep.
  local phase=100011 rel run_dir old_cmd_glide number glide_inputs=()
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  rm -rf "$run_dir" "$run_dir".prev-*
  mkdir -p "$run_dir"
  printf '{"state":"rejected"}\n' >"$run_dir/run.json"
  # The owner's edit, beside the map glide last wrote: the pair differs, which
  # is the evidence an owner edit exists.
  printf '{"version":1,"steps":{"developer":{"harness":"cmd:model-owner"}}}\n' \
    >"$run_dir/harnesses.json"
  printf '{"version":1,"steps":{"developer":{"harness":"cmd:model-driver"}}}\n' \
    >"$run_dir/harnesses.driver.json"
  job_stub_args="$tmp/job-fresh-stub-args"
  rm -f "$job_stub_args"
  old_cmd_glide="$CMD_GLIDE"
  CMD_GLIDE=glide_keep_edit_stub
  number="$phase"
  glide_run_once
  CMD_GLIDE="$old_cmd_glide"
  if grep -q -- '--fresh' "$job_stub_args"; then
    echo "ok: the retry handed --fresh to glide"
  else
    echo "FAIL: the retry did not restart the terminal run with --fresh (stub saw: $(cat "$job_stub_args" 2>/dev/null || echo nothing))"
    fail=1
  fi
  if grep -q 'cmd:model-owner' "$run_dir/harnesses.json"; then
    echo "ok: the new run's harnesses.json keeps the owner's edit"
  else
    echo "FAIL: the retry lost the owner-edited harnesses.json"; fail=1
  fi
  if grep -q "owner-edited $run_dir/harnesses.json" "$LOG"; then
    echo "ok: the detected edit is logged naming the file and the run"
  else
    echo "FAIL: the detected owner edit was not logged"; fail=1
  fi
  rm -rf "$run_dir" "$run_dir".prev-* "$job_stub_args"
}

selftest_case_job_failure() {
  local phase=100010 rel run_dir
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  # The directory a real glide job would have created is not there.
  rm -rf "$run_dir"
  failure_residue_write "$phase" "$rel" blocked 2 3
  if [ -f "$run_dir/failed.json" ] \
      && grep -q "\"phase\": \"$phase\"" "$run_dir/failed.json" \
      && grep -q '"rc": 2' "$run_dir/failed.json"; then
    echo "ok: a failed job is recorded before its run directory exists"
  else
    echo "FAIL: a failed job with no run directory wrote no residue"; fail=1
  fi
  rm -rf "$run_dir"
}
