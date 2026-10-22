# GitHub issue work

Set `driver.provider` to `issues`. Configure `issues` and `board.issues-file` in the same file.

The issue source reads one repository and uses the shared board for claims. It keeps phase state in `board.file` and issue state in a separate file on the same board branch.

See [the example configuration](../examples/issue-config.jsonc). Create a separate board checkout and the configured run and task folders before starting the driver. Keep the task folder inside that board checkout.

Configure a pipeline with the input names in `number-input` and `file-input`. [The example pipeline](../examples/project/issue-pipeline.yaml) has one step that does the fix, its review, and its publication.

A `post_step` hook publishes issue work the same way as phase work. After each step, it runs `gitsync --kind issue --mode checkpoint`, which commits leftovers as a `wip:` commit and pushes them. Then it runs `gitsync --kind issue --mode open-pr --body-file FILE`, which opens or reuses the draft pull request for the issue branch and reads its draft state back. Both modes first check that this machine still holds the issue claim.

Run `argus --config SETTINGS --check`, then `argus --config SETTINGS --dry-run`. Both read the backlog without taking a claim or writing a task file.

The report names the preference, skip reasons, capped results, the next issue, and its run directory. GitHub errors refuse the tick with a reason.

## Selection

Every candidate must be open, pass the author trust gate, and match each label group.

The default `trusted-associations` is `OWNER` and `MEMBER`. An account that joins the organization is trusted on the next read of issues, with no config change, so review this list when membership changes. To drop `MEMBER`, set the list to `OWNER` only. An empty list is a configuration error. `trusted-authors` adds named logins to the trusted set.

A group with `cardinality: one` must match exactly one configured label. A `many` group must match at least one.

`difficulty-order` must contain the same labels as the difficulty group.

`newest` orders by creation time. `easiest` and `hardest` order by the configured difficulty list.

`category` filters by one label and chooses the newest survivor. `search` uses GitHub's best-match order. `category` and `search` can also filter other preferences.

Numeric issue order breaks ties. Blocked, claimed, and escalated work is skipped and counted. An issue that carries the claim label (`claim-label`, default `accepted`) is skipped and counted as `claim_label`, so a person can keep an issue by adding the label. When the board holds or has expired a claim on that issue, the board decides instead: a held claim resumes, and an expired claim can be claimed again.

After the fresh read and the trust check, Argus reserves the issue on the board and then adds the claim label to it on GitHub. If the repository has no label with that name, Argus creates it once and logs `created label`. A failed label write is logged and keeps the claim, because the board reservation is the claim. Argus never removes the label.

Removing the label does not always let Argus pick the issue again. Argus picks it again only when the board has no record for it, or when its expired claim has passed the cooldown. A held claim is left to the board, even without the label. Argus does not pick again an issue whose board record is completed, rejected, or accepted. On this machine, an issue whose job failed and left its claim is skipped too, so the line continues to another issue while the failed claim waits to resume on its own.

A red check on an open issue pull request gets a CI repair round before new work, as for a phase pull request. Only the branch an issue run folder records is read, and only a pull request from this repository: a fork pull request with the same branch name is ignored. See [CI repair rounds](running.md#ci-repair-rounds).

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
| `timeout` | Bound per GitHub call in seconds; defaults to 30. |
| `claim-attempts` | Claim attempts after races; defaults to 3. |
| `claim-label` | The one label that marks a claimed issue; defaults to `accepted`. It is not the board status `accepted`. |

`machine.settings-file` names an optional JSONC overlay.
