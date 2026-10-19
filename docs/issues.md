# GitHub Issue Source (removed)

The GitHub issue work source is removed. A tick runs the phase source only, and `driver.provider` accepts `phase` or `phases`; `issues` is refused at start time with `known drivers: phase, phases`.

The `issues` block is still read for its trust keys (`trusted-authors`, `trusted-apps`, `trusted-associations`, `codeowners-file`, `resolve-teams`, `comment-trust`, `untrusted-comments`), which the pull request reply path uses. Any other key in that block is refused. See [configuration.md](configuration.md) for every key a tick reads.
