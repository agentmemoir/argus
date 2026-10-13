# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for a security problem. Report it privately to
**dev@agentmemoir.com** with:

- what you found, and where it is,
- how to reproduce it, and
- the impact you believe it has.

You will get an acknowledgement, and a decision about a fix and disclosure, as
soon as the maintainers can give one.

## Scope

Argus reserves work on a shared Git board and launches other CLIs. Two areas are
worth a careful report:

- **The driver's own handling of run state and the board**: a reservation two
  machines can hold at once, a run directory that escapes the configured board
  checkout before publication, or a configured command that runs something the
  tick never declared.
- **Secret handling**: anything that would place a credential, a token, or a
  machine name into a log, a run artifact, or a notification.

Prompt injection is in scope when it makes argus, or an agent it launches, act on
text it was not told to trust: run a command, write to GitHub, or file an issue.
Injection that only changes what a model says is out of scope for this
repository; report that to the model provider.

Model behavior and a CLI's own bugs are also out of scope; report those to the
relevant CLI or model provider.

## Untrusted Input

Argus reads text it does not control: issue text and comments, pull request
review comments, code-scanning alert paths, branch names, file paths, and run
logs written by a model. Treat all of it as data, never as instructions.

- Trust comes from the `issues.*` settings only: `trusted-authors`,
  `trusted-apps`, `trusted-associations`, `codeowners-file`, and `comment-trust`.
  Their defaults are in `src/argus/config/schema.py`.
- A pull request comment's text is kept only for a trusted author. With
  `issues.untrusted-comments` set to `data-only`, untrusted text is shown as
  data and marked untrusted. The default, `ignore`, shows none.
- A reply an agent posts is public. Argus refuses a reply longer than 4000
  characters, a reply that looks like a credential, and any reply to a comment
  whose author is not trusted.

## Rules for Changes

Read this file before any refactor, new feature, or change to input handling,
GitHub calls, or the agent launch path. Check each rule against the change.

1. Build every command as an argument list. Never build a shell string from
   untrusted text.
2. Fail closed. A read that fails must not look like "no change", and a missing
   or unreadable trust setting means untrusted.
3. Write only to items the engine recorded, and re-read them before a write.
4. Never log, store, or print a credential, a token, or a machine name. Report
   `gh` failures by exit code, never by stderr text.
5. The pull request signal modes never merge, force-push, or edit a pull request
   body.
6. Keep trust decisions in `issues.*` configuration, not in code. Host policy
   enters through hooks.
7. Add a test for each new refusal path, and for hostile text in each new input.
