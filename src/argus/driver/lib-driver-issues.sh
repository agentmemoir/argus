#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
# Source-specific binding. Argus runs the phase source only.

bind_selection_kind() {
  work_kind=phase
  item_key=phase
  BOARD_KIND_ARGS=()
  GITSYNC_KIND_ARGS=()
}
