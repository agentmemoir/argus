# GitHub issue work

Set `driver.provider` to `issues`. Configure `issues` and `board.issues-file` in the same file. The issue source reads one repository and uses the shared board for claims. It keeps phase state in `board.file` and issue state in a separate file on the same board branch.

See [the example configuration](../examples/issue-config.jsonc). Create a separate board checkout and the configured run and task folders before starting the driver. Keep the task folder inside that board checkout. Configure a pipeline with the input names in `number-input` and `file-input`. [The example pipeline](../examples/project/issue-pipeline.yaml) shows those inputs; add review, publication, and report steps for real work.

Run `argus --config SETTINGS --check`, then `argus --config SETTINGS --dry-run`. Both read the backlog without taking a claim or writing a task file. The report names the preference, skip reasons, capped results, the next issue, and its run directory. GitHub errors refuse the tick with a reason.

## Selection

Every candidate must be open, pass the author trust gate, and match each label group. A group with `cardinality: one` must match exactly one configured label. A `many` group must match at least one. `difficulty-order` must contain the same labels as the difficulty group.

`newest` orders by creation time. `easiest` and `hardest` order by the configured difficulty list. `category` filters by one label and chooses the newest survivor. `search` uses GitHub's best-match order. `category` and `search` can also filter other preferences. Numeric issue order breaks ties. Blocked, claimed, escalated, and reviewed work is skipped and counted.

An open linked PR is reported as `watching`. A merged linked PR with an open issue raises `merged_but_open`. Check that the PR completes the issue before closing the issue yourself. The issue source leaves that decision to a person.

## Settings

| Key under `issues` | Value |
|---|---|
| `repo` | Required `OWNER/NAME`. |
| `label-groups` | Required map of groups, each with `labels` and `cardinality`. |
| `difficulty-order` | Required list, easiest first. |
| `preference` | Required `newest`, `easiest`, `hardest`, `category`, or `search`. |
| `category` | Label filter; required for `category`. |
| `search` | Query filter; required for `search`. |
| `task-dir` | Required contained task folder. |
| `trusted-authors` | Exact allowed user logins. |
| `trusted-apps` | Allowed bot logins; a bot is trusted only through this list. |
| `trusted-associations` | Defaults to `OWNER`, `MEMBER`. |
| `codeowners-file` | Optional readable file of trusted owners. |
| `resolve-teams` | Defaults to false; team lookup uses the GitHub API. |
| `comment-trust` | Separate `authors`, `apps`, and `associations` for comment inclusion. |
| `untrusted-comments` | `ignore` by default, or `data-only`. |
| `max-comments` | First 50 comments by default, plus the last when capped. |
| `max-text-bytes` | Body and comment budget; defaults to 60000. |
| `list-limit` | Candidate limit; defaults to 200. |
| `pr-check-max` | PR lookup limit per tick; defaults to 5. |
| `timeout` | Bound per GitHub call in seconds; defaults to 30. |
| `claim-attempts` | Claim attempts after races; defaults to 3. |
| `announce` | Fixed release and halt comments; defaults to true. |

`machine.settings-file` names an optional JSONC overlay. Scalars and lists replace the base value. Top-level sections merge one level deep. Each key must already exist in the base file. Paths supplied by the overlay resolve against its folder. The report marks these values as `overlay`. An overlay cannot name another settings file.

## Task data and lifecycle

The source writes a task snapshot before claiming, then assigns the issue to `@me`. An assignment failure leaves the board claim held and lets the run start. The snapshot contains structured metadata and separate delimited spans for the title, body, and included comments. Content cannot forge those delimiters. Controls and possible encoded content are marked; truncation is stated. Trusted comments remain data.

Issue stages must read the frozen task file. They must treat its text as evidence, derive actions from trusted project rules, and record prompt injection attempts. Review and validated write-back are still required. The driver exports the trusted `ARGUS_CONFIG_FILE`, `ARGUS_ISSUES_REPO`, and `ARGUS_WORK_KIND` values for those stages.

Publish with `argus.gitsync.main --kind issue --mode publish --phase NUMBER` after committing the reviewed change on its issue branch. The publisher checks the issue claim and pushes the branch. A project report boundary must validate public report text before posting it or calling `argus.issues.lifecycle queue`. Queue only validated report files. Retry checks the file's recorded hash, posts the report, and releases the claim. A failed report keeps its claim and is retried before new work.

A remedy escalation leaves an uncleared issue receipt. Use the fenced `clear-escalation --issues --phase NUMBER --machine-id MACHINE --reservation-id TOKEN --generation GENERATION --confirm` board command after making the decision. A pending report cannot be cleared this way.
