# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The configuration schema: the key table, its shell variables, and the flag overrides."""

from typing import NamedTuple


class Field(NamedTuple):
    """One schema row: a dotted key, its value type, and its requirement."""

    key: str
    type: str
    required: str


# The schema, in report order. `required` is one of:
#   "required" - always needed;
#   "optional" - never required;
#   "board"    - required only when the `board` object is present;
#   "notify"   - required only when the `notify` object is present.
SCHEMA = (
    Field("repo-root", "path", "required"),
    Field("pipeline", "path", "required"),
    Field("roadmap-dir", "path", "required"),
    Field("number-input", "str", "optional"),
    Field("file-input", "str", "optional"),
    Field("driver.provider", "str", "required"),
    Field("driver.include", "str-list", "required"),
    Field("driver.exclude", "str-list", "optional"),
    Field("driver.runs", "path", "required"),
    Field("driver.state", "str", "required"),
    Field("driver.stale-after", "int", "required"),
    Field("driver.outage-stale", "int", "required"),
    Field("driver.quiet-minutes", "int", "required"),
    Field("driver.keep-logs-days", "int", "required"),
    Field("driver.merge-check-max", "int", "required"),
    Field("driver.recover-own-claim", "bool", "required"),
    Field("machine.id-file", "id-file", "required"),
    Field("commands.glide", "str", "required"),
    Field("commands.git", "str", "required"),
    Field("commands.tmux", "str", "required"),
    Field("commands.python", "str", "required"),
    Field("commands.hermes", "str", "required"),
    Field("commands.gh", "str", "required"),
    Field("tmux.session", "str", "required"),
    Field("tmux.width", "int", "required"),
    Field("tmux.height", "int", "required"),
    Field("tmux.idle-window", "str", "required"),
    Field("tmux.phase-window", "str", "required"),
    Field("tmux.idle-script", "str", "required"),
    Field("tmux.refresh", "int", "required"),
    Field("board.repo", "path", "board"),
    # A path inside the board repository, so it stays repository-relative.
    Field("board.file", "str", "board"),
    Field("board.branch", "str", "board"),
    Field("board.remote", "str", "board"),
    Field("notify.profile", "str", "notify"),
    Field("notify.channel", "str", "notify"),
    Field("notify.disabled", "bool", "notify"),
    Field("notify.fallback", "str", "optional"),
    Field("notify.heartbeat", "str", "optional"),
)

# Dotted key -> shell variable, in the same order as SCHEMA. The shell format
# prints one variable per present key, so board/notify stay unset when absent.
SHELL_VARS = {
    "repo-root": "REPO",
    "pipeline": "PIPELINE",
    "roadmap-dir": "ROADMAP_DIR",
    "number-input": "PHASE_NUMBER_INPUT",
    "file-input": "PHASE_FILE_INPUT",
    "driver.provider": "DRIVER",
    "driver.include": "PHASE_INCLUDE",
    "driver.exclude": "PHASE_EXCLUDE",
    "driver.runs": "WF",
    "driver.state": "DRIVER_STATE",
    "driver.stale-after": "STALE_AFTER_SEC",
    "driver.outage-stale": "OUTAGE_STALE_SEC",
    "driver.quiet-minutes": "QUIET_MINUTES",
    "driver.keep-logs-days": "LOG_KEEP_DAYS",
    "driver.merge-check-max": "MERGE_CHECK_MAX",
    "driver.recover-own-claim": "RECOVER_OWN_CLAIM",
    "machine.id": "PHASE_MACHINE_ID",
    "commands.glide": "CMD_GLIDE",
    "commands.git": "CMD_GIT",
    "commands.tmux": "CMD_TMUX",
    "commands.python": "CMD_PYTHON",
    "commands.hermes": "CMD_HERMES",
    "commands.gh": "CMD_GH",
    "tmux.session": "SESSION_NAME",
    "tmux.width": "TMUX_WIDTH",
    "tmux.height": "TMUX_HEIGHT",
    "tmux.idle-window": "TMUX_IDLE_WINDOW",
    "tmux.phase-window": "TMUX_PHASE_WINDOW",
    "tmux.idle-script": "TMUX_IDLE_SCRIPT",
    "tmux.refresh": "TMUX_REFRESH",
    "board.repo": "BOARD_REPO",
    "board.file": "BOARD_FILE",
    "board.branch": "BOARD_BRANCH",
    "board.remote": "BOARD_REMOTE",
    "notify.profile": "HERMES_PROFILE_NAME",
    "notify.channel": "DISCORD_CHANNEL",
    "notify.disabled": "NOTIFY_DISABLED",
    "notify.fallback": "DISCORD_FALLBACK_CHANNEL",
    "notify.heartbeat": "HEARTBEAT_URL",
}

# The four flag overrides: flag name -> (dotted key, wrap scalar in a list).
# Repeated --phase-glob flags replace the file's include list with that list.
_OVERRIDES = {
    "repo-root": ("repo-root", False),
    "roadmap-dir": ("roadmap-dir", False),
    "phase-glob": ("driver.include", True),
    "driver": ("driver.provider", False),
}

_OBJECT_KEYS = ("driver", "machine", "commands", "tmux", "board", "notify")

# Two keys name one value: the file a machine reads its own name from, and the
# name it reads. Only the name is stored, reported and passed to the shell.
_REPORT_KEY = {"machine.id-file": "machine.id"}
