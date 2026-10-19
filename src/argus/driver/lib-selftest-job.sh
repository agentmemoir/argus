#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: a job failure is recorded even when its run directory is absent.
#
# A glide job can fail before it writes run.json, so failure_residue_write has to
# create the directory itself. Without that the residue is never written, the
# selector offers the same phase again, and the line loops on the same failure.

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
