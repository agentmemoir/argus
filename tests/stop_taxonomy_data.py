# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The stop taxonomy rows for the packages the driver tripwire does not scan.

Regenerate by importing the test module's scanner and writing its rows.
"""

ALLOWLIST_ROWS = r"""
    board/branch.py	raise_coordination	raise CoordinationUnavailable("reservation board repository is not configured")
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/branch.py	raise_coordination	raise CoordinationUnavailable(
    board/cli.py	raise_coordination	raise CoordinationError("--machine-id must not be empty")
    board/cli.py	raise_coordination	raise CoordinationError("--reservation-id must not be empty")
    board/cli.py	raise_coordination	raise CoordinationError(
    board/cli_scan.py	raise_coordination	raise CoordinationError("--machine-id must not be empty")
    board/cli_scan.py	raise_coordination	raise CoordinationError("--phase must not be empty")
    board/core.py	raise_coordination	raise CoordinationUnavailable(f"invalid {what}: {value!r}")
    board/core.py	raise_coordination	raise CoordinationUnavailable(f"invalid {what}: {value!r}") from exc
    board/core.py	raise_coordination	raise CoordinationError(f"invalid issue number: {value!r}")
    board/core.py	raise_coordination	raise CoordinationError(f"invalid issue number: {value!r}")
    board/core.py	raise_coordination	raise CoordinationError(f"issue number is out of range: {value!r}")
    board/core.py	raise_coordination	raise CoordinationError(f"invalid phase number: {phase!r}") from None
    board/core.py	raise_coordination	raise CoordinationError(f"phase number out of range: {number}")
    board/core.py	raise_coordination	raise CoordinationError(
    board/core.py	raise_coordination	raise CoordinationError(f"invalid coordination branch: {branch!r}")
    board/core.py	raise_coordination	raise CoordinationError(f"invalid Git remote: {remote!r}")
    board/core.py	raise_coordination	raise CoordinationError(
    board/core.py	raise_coordination	raise CoordinationError("generation must be a positive integer")
    board/core.py	raise_coordination	raise CoordinationError(f"invalid reservation status: {status!r}")
    board/filecommit.py	raise_coordination	raise CoordinationUnavailable(f"cannot stage {named}: {_safe_detail(err)}")
    board/filecommit.py	raise_coordination	raise CoordinationUnavailable(f"cannot commit {named}: {combined}")
    board/filecommit.py	raise_coordination	raise CoordinationUnavailable(f"cannot commit {named}: no revision")
    board/filecommit.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationError(f"invalid reservation kind: {kind!r}")
    board/files.py	raise_coordination	raise CoordinationError(f"publication receipt has invalid {field}")
    board/files.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationError(
    board/files.py	raise_coordination	raise CoordinationError(f"publication receipt cannot be read: {exc}") from exc
    board/files.py	raise_coordination	raise CoordinationError("publication receipt is not a published receipt")
    board/files.py	raise_coordination	raise CoordinationError("publication receipt is not a published receipt")
    board/files.py	raise_coordination	raise CoordinationError("publication receipt phase does not match reservation")
    board/files.py	raise_coordination	raise CoordinationError("publication receipt does not match reservation")
    board/files.py	raise_coordination	raise CoordinationError("publication receipt does not match reservation")
    board/files.py	raise_coordination	raise CoordinationError("publication receipt has no publication time")
    board/files.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationUnavailable(
    board/files.py	raise_coordination	raise CoordinationUnavailable("local and remote publication receipts differ")
    board/files.py	raise_coordination	raise CoordinationError(f"reservation file is unreadable: {exc}") from exc
    board/files.py	raise_coordination	raise CoordinationError("reservation file is not an object")
    board/files.py	raise_coordination	raise CoordinationError(f"reservation file is missing {field}")
    board/git.py	raise_coordination	raise CoordinationUnavailable("git is not on PATH") from exc
    board/git.py	raise_coordination	raise CoordinationUnavailable(
    board/git.py	raise_coordination	raise CoordinationUnavailable(
    board/git.py	raise_coordination	raise CoordinationUnavailable(
    board/git.py	raise_coordination	raise CoordinationUnavailable(
    board/remedy.py	ok_false	print(json.dumps({"ok": False, "error": f"no run directory: {phase_dir}"}))
    board/scan.py	raise_coordination	raise CoordinationUnavailable(f"phase {key}: {exc}") from exc
    board/seed.py	raise_coordination	raise CoordinationUnavailable(
    board/seed.py	raise_coordination	raise CoordinationUnavailable(
    board/state.py	raise_coordination	raise CoordinationError("issue coordination file is not configured")
    board/state.py	raise_coordination	raise CoordinationError("reservation board repository is not configured")
    board/state.py	raise_coordination	raise CoordinationError(
    board/state.py	raise_coordination	raise CoordinationError(
    board/state.py	raise_coordination	raise CoordinationError("issue and phase coordination files must differ")
    board/state.py	raise_coordination	raise CoordinationError(
    board/state.py	raise_coordination	raise CoordinationUnavailable("coordination state is not an object")
    board/state.py	raise_coordination	raise CoordinationUnavailable(
    board/state.py	raise_coordination	raise CoordinationUnavailable("coordination state.phases is not an object")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"invalid {kind} key in state: {key!r}")
    board/state.py	raise_coordination	raise CoordinationUnavailable(str(exc)) from exc
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"invalid issue key in state: {key!r}")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"{kind} {key} record is not an object")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"{kind} {key} has an invalid {field}")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"{kind} {key} has an invalid {field}")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"{kind} {key} has invalid base_revisions")
    board/state.py	raise_coordination	raise CoordinationUnavailable(
    board/state.py	raise_coordination	raise CoordinationUnavailable(
    board/state.py	raise_coordination	raise CoordinationUnavailable("issue coordination has no publication lock")
    board/state.py	raise_coordination	raise CoordinationUnavailable("publication lock is not an object")
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"publication lock is missing {field}")
    board/state.py	raise_coordination	raise CoordinationUnavailable("coordination state file is missing") from exc
    board/state.py	raise_coordination	raise CoordinationUnavailable(f"cannot read coordination state: {exc}") from exc
    board/state.py	raise_coordination	raise CoordinationUnavailable(
    board/store_base.py	raise_coordination	raise CoordinationUnavailable(
    board/store_base.py	raise_coordination	raise CoordinationUnavailable(
    board/store_base.py	raise_coordination	raise CoordinationUnavailable(
    board/store_claim.py	raise_coordination	raise CoordinationError("a new reservation must start as active")
    board/store_claim.py	raise_coordination	raise CoordinationError("registering a running phase requires confirm=true")
    board/store_group.py	raise_coordination	raise CoordinationError("issue reservations are claimed one at a time")
    board/store_group.py	raise_coordination	raise CoordinationError(
    board/store_group.py	raise_coordination	raise CoordinationError("a group claim needs at least one phase")
    board/store_group.py	raise_coordination	raise CoordinationError("a new reservation must start as active")
    board/store_issue.py	raise_coordination	raise CoordinationError("issue receipts require the issue board")
    board/store_issue.py	raise_coordination	raise CoordinationError("reservation must be an object")
    board/store_issue.py	raise_coordination	raise CoordinationError("issue receipt must be an object")
    board/store_issue.py	raise_coordination	raise CoordinationError("issue receipt has an invalid state_reason")
    board/store_issue.py	raise_coordination	raise CoordinationError(
    board/store_issue.py	raise_coordination	raise CoordinationError(
    board/store_issue.py	raise_fenced	raise ReservationFenced(
    board/store_issue.py	raise_fenced	raise ReservationFenced(f"issue {key} is already {record['status']}")
    board/store_issue.py	raise_coordination	raise CoordinationError("issue has no escalation receipt to clear")
    board/store_issue.py	raise_fenced	raise ReservationFenced(f"issue {key} reservation expired")
    board/store_lifecycle.py	raise_coordination	raise CoordinationError("reservation must be an object")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced("reservation has no current members to release")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(f"{self.kind} {key} reservation expired")
    board/store_lifecycle.py	raise_coordination	raise CoordinationError("reservation must be an object")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced("reservation has no current members to renew")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(f"{self.kind} {key} reservation expired")
    board/store_lifecycle.py	raise_coordination	raise CoordinationError("reservation must be an object")
    board/store_lifecycle.py	raise_coordination	raise CoordinationError(
    board/store_lifecycle.py	raise_coordination	raise CoordinationError(
    board/store_lifecycle.py	raise_coordination	raise CoordinationError(
    board/store_lifecycle.py	raise_coordination	raise CoordinationError("issue completion is read from GitHub")
    board/store_lifecycle.py	raise_coordination	raise CoordinationError(
    board/store_lifecycle.py	raise_coordination	raise CoordinationError(
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(f"{self.kind} {key} has no current reservation")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(f"{self.kind} {key} reservation expired")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_lifecycle.py	raise_coordination	raise CoordinationError("reservation must be an object")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(f"{self.kind} {key} reservation expired")
    board/store_lifecycle.py	raise_fenced	raise ReservationFenced(
    board/store_publication.py	raise_coordination	raise CoordinationError("publication locks only apply to phases")
    board/store_publication.py	raise_fenced	raise ReservationFenced(
    board/store_publication.py	raise_fenced	raise ReservationFenced(
    board/store_publication.py	raise_busy	raise PublicationBusy(
    board/store_publication.py	raise_coordination	raise CoordinationError("publication locks only apply to phases")
    board/store_publication.py	raise_fenced	raise ReservationFenced("publication lock belongs to another phase")
    board/store_takeover.py	raise_coordination	raise CoordinationError("explicit operator takeover requires confirm=true")
    board/store_takeover.py	raise_coordination	raise CoordinationError(
    board/store_takeover.py	raise_fenced	raise ReservationFenced(
    board/store_takeover.py	raise_fenced	raise ReservationFenced(
    board/store_takeover.py	raise_busy	raise PublicationBusy(
    board/store_takeover.py	raise_fenced	raise ReservationFenced(
    board/store_takeover.py	raise_fenced	raise ReservationFenced(
    board/worktree.py	raise_coordination	raise CoordinationUnavailable(
    board/worktree.py	raise_coordination	raise CoordinationUnavailable(
    board/worktree.py	raise_coordination	raise CoordinationUnavailable(f"coordination state commit failed: {combined}")
    board/worktree.py	raise_coordination	raise CoordinationUnavailable("coordination commit has no revision")
    board/worktree.py	raise_coordination	raise CoordinationUnavailable(
    config/document.py	raise_config	raise ConfigError("unterminated block comment")
    config/document.py	raise_config	raise ConfigError(f"cannot read config file '{path}': {exc}") from exc
    config/document.py	raise_config	raise ConfigError(f"cannot parse config file '{path}': {exc}") from exc
    config/document.py	raise_config	raise ConfigError(
    config/document.py	raise_config	raise ConfigError(f"config file '{path}' must hold a JSON object")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError("missing required key 'issues.repo'")
    config/issue_validation.py	raise_config	raise ConfigError("missing required key 'board.file' for the issue source")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(f"invalid value for key '{key}': expected string list")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(f"invalid value for key '{key}': repeated label")
    config/issue_validation.py	raise_config	raise ConfigError(f"unknown key '{key}'")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError("invalid value for key 'issues.repo': expected OWNER/NAME")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(f"missing required key '{key}' for preference '{name}'")
    config/issue_validation.py	raise_config	raise ConfigError(f"invalid value for key '{key}': expected non-empty text")
    config/issue_validation.py	raise_config	raise ConfigError(f"invalid value for key '{key}': must be positive")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(f"invalid label group '{key}': expected object")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(f"unknown key '{key}.{sorted(unknown)[0]}'")
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(
    config/issue_validation.py	raise_config	raise ConfigError(
    config/loader.py	raise_config	raise ConfigError(
    config/loader.py	raise_config	raise ConfigError(f"cannot read config file '{path}': not a file")
    config/loader.py	raise_config	raise ConfigError(f"invalid type for key '{name}': expected object")
    config/loader.py	raise_config	raise ConfigError(f"unknown override '{flag}'")
    config/loader.py	raise_config	raise ConfigError(f"missing required key '{field.key}'")
    config/loader.py	ok_false	ok = False
    config/loader.py	raise_config	raise ConfigError(f"invalid type for key '{field.key}': expected {kind}")
    config/loader.py	raise_config	raise ConfigError(
    config/loader.py	raise_config	raise ConfigError(f"machine id file '{path}' is empty")
    config/loader.py	raise_config	raise ConfigError(
    config/loader.py	raise_config	raise ConfigError(f"missing required key '{field.key}'")
    config/loader.py	raise_config	raise ConfigError(
    config/overlay.py	raise_config	raise ConfigError(
    config/overlay.py	raise_config	raise ConfigError(f"overlay key '{section}' is absent from the base file")
    config/overlay.py	raise_config	raise ConfigError(
    config/overlay.py	raise_config	raise ConfigError("overlay may not name 'machine.settings-file'")
    config/overlay.py	raise_config	raise ConfigError(
    config/phases_validation.py	raise_config	raise ConfigError("invalid value for key 'phases.repo': not a Git work tree")
    config/phases_validation.py	raise_config	raise ConfigError(
    config/phases_validation.py	raise_config	raise ConfigError(
    config/phases_validation.py	raise_config	raise ConfigError(
    issues/claims.py	raise_coordination	raise CoordinationError("could not persist issue reservation token") from exc
    issues/order.py	raise_value	raise ValueError("timestamp needs timezone")
    issues/source.py	raise_value	raise ValueError(
    select/group.py	raise_coordination	raise CoordinationError(
    select/group.py	raise_coordination	raise CoordinationError(
"""
