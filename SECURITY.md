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

Model behavior, a CLI's own bugs, and prompt injection that only affects the
model are out of scope for this repository; report those to the relevant CLI or
model provider.
