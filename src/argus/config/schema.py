# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
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
#   "phases"   - required only when the work source is the phase source;
#   "notify"   - required only when the `notify` object is present;
#   "issues"   - required only when the `issues` object is present;
#   "runstate" - required only when the `runstate` object is present.
SCHEMA = (
    Field("repo-root", "path", "required"),
    Field("pipeline", "path", "required"),
    # The phases repository, a peer of the board. `repo` is a local checkout,
    # `directory` a folder inside it, and branch/remote name where Argus commits
    # and pushes. Required for the phase source.
    Field("phases.repo", "path", "phases"),
    Field("phases.directory", "str", "phases"),
    Field("phases.branch", "str", "phases"),
    Field("phases.remote", "str", "phases"),
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
    Field("machine.settings-file", "path", "optional"),
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
    Field("board.issues-file", "str", "issues"),
    Field("board.branch", "str", "board"),
    Field("board.remote", "str", "board"),
    Field("board.backup-branch", "str", "optional"),
    # Where run state is committed and confirmed: `repo` is the checkout that
    # holds the run directory, `remote` the remote to push and confirm against,
    # and `branch` the integration branch carrying the receipt. Optional as a
    # whole; without it Argus derives the checkout from `driver.runs`.
    Field("runstate.repo", "path", "runstate"),
    Field("runstate.remote", "str", "runstate"),
    Field("runstate.branch", "str", "runstate"),
    Field("notify.profile", "str", "notify"),
    Field("notify.channel", "str", "notify"),
    Field("notify.disabled", "bool", "notify"),
    Field("notify.fallback", "str", "optional"),
    Field("notify.heartbeat", "str", "optional"),
    Field("issues.repo", "str", "issues"),
    Field("issues.label-groups", "object", "issues"),
    Field("issues.difficulty-order", "str-list", "issues"),
    Field("issues.preference", "str", "issues"),
    Field("issues.category", "str", "optional"),
    Field("issues.search", "str", "optional"),
    Field("issues.task-dir", "path", "issues"),
    Field("issues.trusted-authors", "str-list", "optional"),
    Field("issues.trusted-apps", "str-list", "optional"),
    Field("issues.trusted-associations", "str-list", "optional"),
    Field("issues.codeowners-file", "path", "optional"),
    Field("issues.resolve-teams", "bool", "optional"),
    Field("issues.comment-trust", "object", "optional"),
    Field("issues.untrusted-comments", "str", "optional"),
    Field("issues.max-comments", "int", "optional"),
    Field("issues.max-text-bytes", "int", "optional"),
    Field("issues.list-limit", "int", "optional"),
    Field("issues.timeout", "int", "optional"),
    Field("issues.claim-attempts", "int", "optional"),
    # The CI repair round: the one-step pipeline that fixes red checks on an
    # open driver pull request, how many rounds run, and the seconds added
    # before each later round. Defaults apply when the keys are absent.
    Field("repair.pipeline", "path", "optional"),
    Field("repair.attempts", "int", "optional"),
    Field("repair.wait", "int", "optional"),
    Field("issues.claim-label", "str", "optional"),
    Field("estimate.factors-file", "path", "optional"),
    Field("estimate.ideal-days", "str", "optional"),
)

# Dotted key -> shell variable, in the same order as SCHEMA. The shell format
# prints one variable per present key, so board/notify stay unset when absent.
SHELL_VARS = {
    "repo-root": "REPO",
    "pipeline": "PIPELINE",
    "phases.repo": "PHASES_REPO",
    "phases.directory": "PHASES_DIRECTORY",
    "phases.branch": "PHASES_BRANCH",
    "phases.remote": "PHASES_REMOTE",
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
    "machine.settings-file": "MACHINE_SETTINGS_FILE",
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
    "board.issues-file": "BOARD_ISSUES_FILE",
    "board.branch": "BOARD_BRANCH",
    "board.remote": "BOARD_REMOTE",
    "board.backup-branch": "BOARD_BACKUP_BRANCH",
    "runstate.repo": "RUNSTATE_REPO",
    "runstate.remote": "RUNSTATE_REMOTE",
    "runstate.branch": "RUNSTATE_BRANCH",
    "notify.profile": "HERMES_PROFILE_NAME",
    "notify.channel": "DISCORD_CHANNEL",
    "notify.disabled": "NOTIFY_DISABLED",
    "notify.fallback": "DISCORD_FALLBACK_CHANNEL",
    "notify.heartbeat": "HEARTBEAT_URL",
    "repair.pipeline": "REPAIR_PIPELINE",
    "repair.attempts": "REPAIR_ATTEMPTS",
    "repair.wait": "REPAIR_WAIT",
    "estimate.factors-file": "ESTIMATE_FACTORS_FILE",
    "estimate.ideal-days": "ESTIMATE_IDEAL_DAYS",
}
SHELL_VARS.update(
    {
        field.key: field.key.replace(".", "_").replace("-", "_").upper()
        for field in SCHEMA
        if field.key.startswith("issues.")
    }
)

ISSUES_DEFAULTS: dict[str, object] = {
    "issues.trusted-authors": [],
    "issues.trusted-apps": [],
    "issues.trusted-associations": ["OWNER", "MEMBER"],
    "issues.resolve-teams": False,
    "issues.untrusted-comments": "ignore",
    "issues.max-comments": 50,
    "issues.max-text-bytes": 60000,
    "issues.list-limit": 200,
    "issues.timeout": 30,
    "issues.claim-attempts": 3,
    "issues.claim-label": "accepted",
}

# Three rounds: now, after 10 minutes, after 20 minutes. Then Needs You.
REPAIR_DEFAULTS: dict[str, object] = {
    "repair.attempts": 3,
    "repair.wait": 600,
}

# The four flag overrides: flag name -> (dotted key, wrap scalar in a list).
# Repeated --phase-glob flags replace the file's include list with that list.
_OVERRIDES = {
    "repo-root": ("repo-root", False),
    "phases-directory": ("phases.directory", False),
    "phase-glob": ("driver.include", True),
    "driver": ("driver.provider", False),
}

_OBJECT_KEYS = (
    "driver",
    "machine",
    "commands",
    "tmux",
    "board",
    "phases",
    "runstate",
    "notify",
    "issues",
    "repair",
    "estimate",
)

# Two keys name one value: the file a machine reads its own name from, and the
# name it reads. Only the name is stored, reported and passed to the shell.
_REPORT_KEY = {"machine.id-file": "machine.id"}


# The schema, the shell variable map, and the constants the loader, the
# renderers, and the shell helpers read. Declared so which names other modules
# may read is a statement rather than an accident of a leading underscore.
__all__ = [
    "Field",
    "SCHEMA",
    "SHELL_VARS",
    "REPAIR_DEFAULTS",
    "_OBJECT_KEYS",
    "_OVERRIDES",
    "_REPORT_KEY",
]
