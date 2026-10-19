# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Tripwire: a new non-owned stop in the driver or gitsync fails this test.

The allowlist is keyed by file, rule, and the normalized stop line, never by a
line number, and every rule carries one owner reason. A stop that sits behind a
helper is attributed to the helper's callers. Add a stop and this test fails
until an owner reason is recorded here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/argus"
DRIVER = ROOT / "driver"
GITSYNC = ROOT / "gitsync"

SHELL_RULES = {
    "exit_nonzero": re.compile(r"(?<![\w-])exit\s+([1-9][0-9]*)\b"),
    "return_nonzero": re.compile(r"(?<![\w-])return\s+([1-9][0-9]*)\b"),
    "drive_rc_nonzero": re.compile(r"_DRIVE_RC=([1-9][0-9]*)\b"),
    "drive_rc_zero_refusal": re.compile(r"_DRIVE_RC=0\b"),
    "halt_marker": re.compile(r">\s*\"\$HALT\""),
    "resume_refuse": re.compile(r"(?<![\w-])resume_refuse\b"),
}

PY_RULES = {
    "raise_fail": re.compile(r"raise\s+Fail\("),
    "raise_config": re.compile(r"raise\s+(ConfigError|ConfigException)\("),
    "raise_coordination": re.compile(
        r"raise\s+(CoordinationError|CoordinationUnavailable)\("
    ),
    "raise_fenced": re.compile(r"raise\s+ReservationFenced\("),
    "raise_busy": re.compile(r"raise\s+PublicationBusy\("),
    "blocked_call": re.compile(r"(?<![\w.])blocked\("),
    "ok_false": re.compile(r"\bok[\"']?\s*[:=]\s*False|ok=False"),
}

REFUSAL_WORDS = re.compile(
    r"refus|no launch|not runnable|halt|no new work|stop|block", re.IGNORECASE
)
HELPER_STOPS = {"resume_refuse", "driver_orphan_check", "on_stop"}

REASONS = {
    "exit_nonzero": "operator command, usage, broken install, or manual resume; not a tick stop",
    "return_nonzero": "a helper signal or an owned reservation/running-job check; not a run stop",
    "drive_rc_nonzero": "owned reservation, running job, or own-state sanity check",
    "drive_rc_zero_refusal": "a no-launch tick that warns and continues",
    "halt_marker": "operator stop, signal to a running job, orphaned run, or configuration halt",
    "resume_refuse": "owned reservation, running job, own state, or a usage error on --resume",
    "raise_fail": "glide or an owned run-state check; the caller repairs or retries",
    "raise_config": "start-time configuration or usage error; names the key and the fix",
    "raise_coordination": "owned reservation board; retries next tick",
    "raise_fenced": "owned reservation fence; retries next tick",
    "raise_busy": "owned publication lock; retries next tick",
    "blocked_call": "narrow lost-work stop, start-time configuration, or a publication retry",
    "ok_false": "retry or refusal signal for a caller that reports and continues",
}

_ALLOWLIST = r"""
    driver/lib-driver-abandon.sh	exit_nonzero	driver_lock_take || { printf 'argus abandon: another driver instance holds the lock; try again\n' >&2; exit 1; }
    driver/lib-driver-abandon.sh	exit_nonzero	driver_orphan_check || { printf 'argus abandon: a phase run is alive with no window (orphaned); not abandoning\n' >&2; exit 1; }
    driver/lib-driver-abandon.sh	exit_nonzero	exit 1
    driver/lib-driver-abandon.sh	exit_nonzero	exit 1 ;;
    driver/lib-driver-abandon.sh	exit_nonzero	exit 2
    driver/lib-driver-abandon.sh	exit_nonzero	exit 2 ;;
    driver/lib-driver-check.sh	return_nonzero	return 1
    driver/lib-driver-config.sh	exit_nonzero	*) printf 'argus: unknown driver %s; known drivers: phase, issues\n' "$DRIVER" >&2; exit 2 ;;
    driver/lib-driver-config.sh	exit_nonzero	exit 3
    driver/lib-driver-config.sh	return_nonzero	*) return 1 ;;
    driver/lib-driver-config.sh	return_nonzero	[ -n "$candidate" ] && [ -x "$candidate" ] || return 1
    driver/lib-driver-config.sh	return_nonzero	if ! out="$(run_config "$@")"; then return 2; fi
    driver/lib-driver-config.sh	return_nonzero	return 1
    driver/lib-driver-drive.sh	drive_rc_nonzero	tidy_published_checkout || { _DRIVE_RC=1; return 0; }
    driver/lib-driver-drive.sh	halt_marker	[ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
    driver/lib-driver-drive.sh	halt_marker	helper:driver_orphan_check|[ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
    driver/lib-driver-drive.sh	halt_marker	printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"
    driver/lib-driver-drive.sh	return_nonzero	flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }
    driver/lib-driver-drive.sh	return_nonzero	helper:driver_orphan_check|return 1
    driver/lib-driver-drive.sh	return_nonzero	reconcile_pending_reviews || return 1
    driver/lib-driver-drive.sh	return_nonzero	return 1
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	{ _DRIVE_RC=0; return 0; }
    driver/lib-driver-fence.sh	return_nonzero	reserve|fail) return 1 ;;
    driver/lib-driver-fence.sh	return_nonzero	return 1
    driver/lib-driver-fence.sh	return_nonzero	return 1 ;;
    driver/lib-driver-group.sh	return_nonzero	return 1
    driver/lib-driver-halt-record.sh	halt_marker	helper:resume_refuse|printf 'resume refused: %s\n' "$reason" >"$HALT"
    driver/lib-driver-halt-record.sh	resume_refuse	helper:resume_refuse|resume_refuse() { # <phase> <reason>
    driver/lib-driver-halt-record.sh	return_nonzero	)" || return 1
    driver/lib-driver-halt-record.sh	return_nonzero	return 1
    driver/lib-driver-issues.sh	return_nonzero	return 1
    driver/lib-driver-launch.sh	drive_rc_nonzero	|| { notify "${work_kind:-phase} $number failed to create tmux session '$SESSION'"; { _DRIVE_RC=1; return 0; }; }
    driver/lib-driver-launch.sh	drive_rc_nonzero	|| { notify "${work_kind:-phase} $number failed to launch in tmux"; { _DRIVE_RC=1; return 0; }; }
    driver/lib-driver-log.sh	return_nonzero	printf -v byte '%d' "'$last" || return 1
    driver/lib-driver-maintenance.sh	return_nonzero	tidy_published_checkout || return 1
    driver/lib-driver-resume.sh	exit_nonzero	driver_lock_take || { printf 'argus: another driver instance holds the lock; try again\n' >&2; exit 1; }
    driver/lib-driver-resume.sh	exit_nonzero	driver_orphan_check || { printf 'argus: a phase run is alive with no window (orphaned); the line is halted\n' >&2; exit 1; }
    driver/lib-driver-resume.sh	exit_nonzero	exit 1
    driver/lib-driver-resume.sh	exit_nonzero	exit 2 ;;
    driver/lib-driver-resume.sh	resume_refuse	resume_refuse "$target" "$PREFLIGHT_REASON"
    driver/lib-driver-resume.sh	resume_refuse	resume_refuse "$target" "$RESUME_REFUSAL"
    driver/lib-driver-resume.sh	resume_refuse	resume_refuse "$target" "no phase file found for phase $number"
    driver/lib-driver-resume.sh	resume_refuse	resume_refuse "$target" "the halt record $HALT_RECORD is missing or malformed"
    driver/lib-driver-resume.sh	resume_refuse	resume_refuse "$target" "the launch failed; see $RUN_DIR/driver.log"
    driver/lib-driver-resume.sh	return_nonzero	return 1
    driver/lib-driver-run.sh	halt_marker	"${work_kind:-phase}" "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    driver/lib-driver-run.sh	halt_marker	"${work_kind:-phase}" "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
    driver/lib-driver-run.sh	halt_marker	helper:on_stop|"${work_kind:-phase}" "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    driver/lib-driver-run.sh	return_nonzero	IFS= read -r line <"$1" || return 1
    driver/lib-driver-run.sh	return_nonzero	[ -s "$1" ] || return 1
    driver/lib-driver-run.sh	return_nonzero	return 1
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	{ _DRIVE_RC=1; return 0; }
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	{ _DRIVE_RC=0; return 0; }
    driver/lib-driver-select-real.sh	drive_rc_nonzero	{ _DRIVE_RC=1; return 0; }
    driver/lib-driver-select.sh	return_nonzero	return 1
    driver/phase-driver.sh	exit_nonzero	[ "$#" -eq 3 ] || { echo "usage: phase-driver.sh --session <N> <phase-file>" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	[ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'argus: %s needs a value\n' "$1" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	[ "$#" -ge 2 ] || { echo "usage: phase-driver.sh abandon <NUMBER> [--reason TEXT]" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	[ "$#" -le 1 ] || { echo "usage: phase-driver.sh --resume [NUMBER]" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	exit 2
    driver/phase-driver.sh	exit_nonzero	exit 3
    driver/phase-tmux-idle.sh	exit_nonzero	CONFIG_OUT="$("$PYTHON" "$PKG_DIR/config/cli.py" "$@" --format shell)" || exit 2
    driver/phase-tmux-idle.sh	exit_nonzero	exit 2
    driver/phase-tmux.sh	return_nonzero	"$TMUX_BIN" new-window -d -t "$SESSION" -n "$TMUX_PHASE_WINDOW" -c "$REPO" "$launch" || return 1
    driver/phase-tmux.sh	return_nonzero	"$TMUX_BIN" send-keys -t "$wid" C-c 2>/dev/null || return 1
    driver/phase-tmux.sh	return_nonzero	[ "$dead" = "1" ] && return 1
    driver/phase-tmux.sh	return_nonzero	return 1
    driver/phase-tmux.sh	return_nonzero	wid="$(tmux_window_id)" || return 1
    driver/phase-tmux.sh	return_nonzero	|| { log "failed to create tmux session '$SESSION'"; return 1; }
    gitsync/branch.py	blocked_call	return blocked(
    gitsync/git.py	blocked_call	def blocked(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    gitsync/git.py	raise_config	raise ConfigError("git is not on PATH") from exc
    gitsync/group_publication.py	blocked_call	return blocked(
    gitsync/group_publication.py	blocked_call	return blocked({"repo": str(repo)}, why)
    gitsync/group_publication.py	raise_config	raise ConfigError(
    gitsync/group_publication.py	raise_config	raise ConfigError(f"cannot read phase reservation: {exc}") from exc
    gitsync/group_publication.py	raise_config	raise ConfigError(f"phase run directory is missing: {run_dir}")
    gitsync/group_review.py	ok_false	"ok": False,
    gitsync/issue_modes.py	ok_false	"ok": False,
    gitsync/issue_modes.py	raise_config	raise ConfigError(
    gitsync/issue_modes.py	raise_config	raise ConfigError("--phase is required with issue publish mode")
    gitsync/issue_modes.py	raise_config	raise ConfigError("branch task filename differs from the frozen issue task")
    gitsync/issue_modes.py	raise_config	raise ConfigError("issue branches do not accept phase groups")
    gitsync/issue_paths.py	raise_config	raise ConfigError(
    gitsync/issue_paths.py	raise_config	raise ConfigError("current issue has no valid frozen task snapshot")
    gitsync/issue_paths.py	raise_config	raise ConfigError("issue data must be inside the private checkout")
    gitsync/issue_paths.py	raise_config	raise ConfigError("issue publication needs a separate private checkout")
    gitsync/issue_paths.py	raise_config	raise ConfigError("issue reservation file must be inside the current run")
    gitsync/issue_paths.py	raise_config	raise ConfigError("issue task filename does not match the issue number")
    gitsync/issue_paths.py	raise_config	raise ConfigError("public checkout tracks private issue data")
    gitsync/issue_paths.py	raise_config	raise ConfigError("reservation does not belong to the current issue")
    gitsync/issue_paths.py	raise_config	raise ConfigError(str(exc)) from exc
    gitsync/main.py	ok_false	"ok": False,
    gitsync/main.py	raise_config	raise ConfigError(
    gitsync/main.py	raise_config	raise ConfigError("--phase is required with --mode publish")
    gitsync/main.py	raise_config	raise ConfigError("--phase is required with --mode published")
    gitsync/main.py	raise_config	raise ConfigError("--phase is required with --mode signals")
    gitsync/merge.py	blocked_call	return blocked(
    gitsync/modes.py	ok_false	"ok": False,
    gitsync/paths.py	raise_config	raise ConfigError(
    gitsync/paths.py	raise_config	raise ConfigError("a group branch needs at least one member")
    gitsync/paths.py	raise_config	raise ConfigError(f"not a phase number: {member!r}") from exc
    gitsync/paths.py	raise_config	raise ConfigError(f"phase file carries no behaviour: {phases_name!r}")
    gitsync/pr_signal_reply.py	ok_false	return {"ok": False, "state": "REFUSED", "reasons": [reason], "events": []}
    gitsync/pr_signals.py	ok_false	return {"ok": False, "state": "UNAVAILABLE", "reasons": [reason], "events": []}
    gitsync/pr_signals.py	ok_false	return {"ok": False, "state": "UNAVAILABLE", "reasons": [why], "events": []}
    gitsync/publish.py	blocked_call	return blocked(
    gitsync/publish_issue.py	ok_false	info, ok=False, state="RETRY", summary=summarize([clean], "publish")
    gitsync/publish_issue.py	ok_false	ok=False,
    gitsync/publish_issue.py	ok_false	return dict(info, ok=False, state="COORDINATION_UNAVAILABLE", error=str(exc))
    gitsync/publish_issue.py	ok_false	return dict(info, ok=False, state="FENCED", error=str(exc))
    gitsync/publish_issue.py	ok_false	return dict(info, ok=False, state="RETRY", summary=resync["summary"])
    gitsync/publish_issue.py	ok_false	return dict(info, ok=False, state="RETRY", summary=synced["summary"])
    gitsync/publish_issue.py	raise_config	raise ConfigError("frozen issue task does not belong to the current claim")
    gitsync/publish_issue.py	raise_config	raise ConfigError("public checkout is not on the current issue branch")
    gitsync/publish_issue.py	raise_config	raise ConfigError(why or "issue upstream names a different branch")
    gitsync/publish_phase.py	ok_false	"ok": False,
    gitsync/publish_phase.py	ok_false	outcome or {"ok": False, "mode": "publish", "phase": f"{int(phase):06d}"}
    gitsync/publish_phase.py	raise_config	raise ConfigError("publication requires phase-scoped run-state commit")
    gitsync/publish_phase.py	raise_config	raise ConfigError(f"invalid phase number: {phase!r}") from exc
    gitsync/publish_phase.py	raise_config	raise ConfigError(f"phase number out of range: {phase}")
    gitsync/runstate.py	blocked_call	blocked(
    gitsync/runstate.py	blocked_call	return blocked(
    gitsync/runstate.py	ok_false	"ok": False,
    gitsync/runstate.py	raise_config	raise ConfigError(
    gitsync/runstate.py	raise_config	raise ConfigError(f"invalid phase number: {phase!r}") from None
    gitsync/runstate.py	raise_config	raise ConfigError(f"phase number out of range: {number}")
    gitsync/sync.py	blocked_call	blocked({"repo": str(path), "mode": mode}, reason)
    gitsync/sync.py	blocked_call	blocked_result = blocked(
    gitsync/sync.py	blocked_call	return blocked(
    gitsync/sync.py	ok_false	"ok": False,
    gitsync/tidy.py	blocked_call	return blocked(
"""


def _allowed() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for line in _ALLOWLIST.strip().splitlines():
        rel, rule, anchor = line.strip().split("\t", 2)
        out.add((rel, rule, anchor))
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())[:200]


def _spans(text: str) -> list[tuple[str, int, int]]:
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", line)
        if match:
            starts.append((match.group(1), i))
    out: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else 10**9
        out.append((name, start, end))
    return out


def _callers(texts: dict[str, str], name: str) -> list[str]:
    found: list[str] = []
    for _rel, text in texts.items():
        for other, start, end in _spans(text):
            if other == name:
                continue
            body = "\n".join(text.splitlines()[start:end])
            if re.search(rf"(?<![\w-]){re.escape(name)}\b", body):
                found.append(other)
    return sorted(set(found))


def _scan(
    driver: Path = DRIVER, gitsync: Path = GITSYNC
) -> list[tuple[str, str, str, str, list[str]]]:
    findings: list[tuple[str, str, str, str, list[str]]] = []
    shell_texts = {
        f"driver/{path.name}": path.read_text()
        for path in sorted(driver.glob("*.sh"))
        if "selftest" not in path.name and path.name != "selftest-support.sh"
    }
    for rel, text in shell_texts.items():
        spans = _spans(text)
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#"):
                continue
            owner = ""
            for name, start, end in spans:
                if start <= i < end:
                    owner = name
            for rule, pattern in SHELL_RULES.items():
                if not pattern.search(line):
                    continue
                if rule == "drive_rc_zero_refusal":
                    window = " ".join(text.splitlines()[max(0, i - 2) : i + 1])
                    if not REFUSAL_WORDS.search(window):
                        continue
                anchor = _normalize(line)
                helper = owner if owner in HELPER_STOPS else ""
                if helper:
                    anchor = f"helper:{helper}|{anchor}"
                callers = _callers(shell_texts, owner) if helper else []
                findings.append((rel, rule, anchor, helper, callers))
    py_texts = {
        f"gitsync/{path.name}": path.read_text()
        for path in sorted(gitsync.glob("*.py"))
        if "selftest" not in path.name
    }
    for rel, text in py_texts.items():
        for line in text.splitlines():
            if line.strip().startswith("#"):
                continue
            for rule, pattern in PY_RULES.items():
                if pattern.search(line):
                    findings.append((rel, rule, _normalize(line), "", []))
    return findings


def test_no_unowned_stop_appears() -> None:
    allowed = _allowed()
    unexpected = [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan()
        if (rel, rule, anchor) not in allowed
    ]
    assert not unexpected, (
        "a new stop path must be removed or allowlisted with an owner reason in "
        f"{__file__}: {unexpected}"
    )


def test_every_allowed_rule_has_an_owner_reason() -> None:
    for _rel, rule, _anchor in _allowed():
        assert rule in REASONS, f"allowlisted rule {rule!r} has no owner reason"


def test_a_helper_stop_names_its_callers() -> None:
    helpers = [f for f in _scan() if f[3] == "resume_refuse"]
    assert helpers, "the resume_refuse helper stop was not found"
    for _rel, _rule, _anchor, _helper, callers in helpers:
        assert "_drive_resume" in callers, callers


def test_an_injected_stop_fails_the_tripwire(tmp_path: Path) -> None:
    driver = tmp_path / "driver"
    driver.mkdir()
    (driver / "lib-driver-new.sh").write_text("new_helper() { return 1; }\n")
    injected = [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan(
            driver=driver, gitsync=tmp_path
        )
        if (rel, rule, anchor) not in _allowed()
    ]
    assert (
        "driver/lib-driver-new.sh",
        "return_nonzero",
        "new_helper() { return 1; }",
    ) in injected
