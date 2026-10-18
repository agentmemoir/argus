# Security review: the `show` filters

This page records the `SECURITY.md` "Rules for Changes" review of the two inputs that `python -m argus.board.reservations show` gained: the repeatable `--reservation-id` and the single `--machine-id`. [PR #132](https://github.com/agentmemoir/argus/pull/132) implements the filters. This page is the security-review item from [issue #134](https://github.com/agentmemoir/argus/issues/134); [issue #133](https://github.com/agentmemoir/argus/issues/133) is not closed by this page, because its second defect is a host-side scan rule handled separately. Each of the seven numbered rules is checked below, with what this review found for these two inputs.

Both values arrive as `argv` tokens. The `show` parser records `--reservation-id` with `action="append"` and `--machine-id` plainly; neither has a default, so `None` means "not given". `_show_filters` reads them, refuses a blank value, and strips padding; `_narrow_state` compares the stripped values to the board records it already read. No code path turns either value into a command, a path, or a credential.

## Rule 1 — build commands as argument lists, never a shell string

Rule: "Build every command as an argument list. Never build a shell string from untrusted text." Finding: the two values never reach a command. The only use is a set membership test and an equality test inside `_narrow_state`, against values already held in the board dictionary. No shell string and no subprocess is made from them, so the rule holds by construction.

## Rule 2 — fail closed

Rule: "Fail closed. A read that fails must not look like 'no change', and a missing or unreadable trust setting means untrusted." Finding: a blank value is refused with exit 2 before the board is read, so a bad input cannot silently print the whole board or an empty view. An id the board does not hold is absent from the answer, which is the designed shape: the output keeps the board's `version`, so an empty phase map reads as "this id matched nothing" rather than as a lost read. A board read that fails raises `CoordinationUnavailable` or `CoordinationError`, which the CLI prints as `COORDINATION_UNAVAILABLE` with exit 2; it does not read as an empty board. The config load reads the whole config file, including `issues.*`, but `show` makes no trust decision, so the trust half of this rule is not exercised here.

## Rule 3 — write only to recorded items, re-read before a write

Rule: "Write only to items the engine recorded, and re-read them before a write." Finding: `show` is read-only. `_narrow_state` returns a new dictionary and never writes back to the board. The tests read the board unfiltered again after a filter and prove it is unchanged, including after a hostile id. The two inputs never reach a write path.

## Rule 4 — no credential, token, or machine name

Rule: "Never log, store, or print a credential, a token, or a machine name. Report `gh` failures by exit code, never by stderr text. Trusted comment text is stored in the run folder and shown to the agent as data; it is not scanned for secrets." Finding: this is the rule with a real nuance. Yes, a board `machine_id` counts as a machine name for this rule. The board already holds `machine_id` on each phase record and on the publication lock, and `show` prints the board it reads, so a machine id is printed whether or not `--machine-id` is passed. The new flag only narrows which records print, so it can only print fewer machine ids than the unfiltered view. That print is pre-existing behavior the review flags; the new input does not introduce it. On the error path the refusal names the flag alone (`--machine-id must not be empty`), never the supplied text, so a supplied value never reaches the printed view when it is rejected. Neither value is logged, stored, or written to a run artifact. `show` calls no `gh` command, prints no credential or token, and does not touch trusted comment text.

## Rule 5 — the pull request signal modes

Rule: "The pull request signal modes never merge, force-push, or edit a pull request body." Finding: not implicated. `show` does not touch pull requests; the two inputs only narrow the board view. The signal modes are a different subsystem.

## Rule 6 — keep trust decisions in configuration

Rule: "Keep trust decisions in `issues.*` configuration, not in code. Host policy enters through hooks." Finding: not implicated. `show` makes no trust decision. The two inputs select board records by identity; they do not decide whether anything is trusted. Trust policy is read by the issue work source, not by `show`.

## Rule 7 — a test for each refusal and each hostile input

Rule: "Add a test for each new refusal path, and for hostile text in each new input." Finding: covered in `tests/test_board_show_filters.py`. A blank or whitespace-only value for each flag is refused with exit 2, and a reservation id of a newline with tabs is refused too. Hostile text is covered for each input over the same eight shapes — command substitution, backticks, a shell separator, a pipe, a path traversal, an absolute path, a newline, and a very long string — and each is treated as an opaque id that matches nothing while the board read stays untouched.

## Summary

Rules 3, 5, and 6 are not implicated by these two inputs. Rules 1 and 2 are implicated and hold: rule 1 holds by construction because the two values never reach a command, and rule 2 holds because a bad input is refused with exit 2 and a failed board read does not read as an empty board. Rule 4 is implicated: a board `machine_id` counts as a machine name, and `show` already printed machine ids on each phase record and the publication lock before these inputs; the new flag only narrows which records print, so it cannot introduce a machine name, but the review flags the pre-existing print. Rule 7 is satisfied by the tests that ship with the change.
