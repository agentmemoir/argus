# GitHub issue work

Set `driver.provider` to `issues`. Configure `issues` and `board.issues-file` in the same file.

The issue source reads one repository and uses the shared board for claims. It keeps phase state in `board.file` and issue state in a separate file on the same board branch.

See [the example configuration](../examples/issue-config.jsonc). Create a separate board checkout and the configured run folder before starting the driver. Keep the task folder inside that board checkout. [Set up an issue driver host](issue-driver-setup.md) gives the steps for a new host.

Configure a pipeline with the input names in `number-input` and `file-input`. [The example pipeline](../examples/project/issue-pipeline.yaml) has one step that does the fix, its review, and its publication.

Run `argus --config SETTINGS --check`, then `argus --config SETTINGS --dry-run`. Both read the backlog without taking a claim or writing a task file.

The report names the preference, skip reasons, capped results, the next issue, and its run directory. GitHub errors refuse the tick with a reason.

## Selection

Argus takes the first candidate in the preference order that passes every check. A candidate must be open, pass the author trust gate, and match each label group.

`preference` sets the order. `hardest` starts with the last label in `difficulty-order`, and `easiest` starts with the first. `newest` starts with the most recent issue. `category` keeps only issues that carry one label, then picks the newest. `search` keeps GitHub's best-match order. When two issues tie, the lower issue number goes first. `category` and `search` can also filter the other preferences.

A group with `cardinality: one` must match exactly one configured label. A `many` group must match at least one. `difficulty-order` must contain the same labels as the difficulty group.

## Trust

Outsider issues never reach the agent. An issue whose author is not trusted is skipped and counted as `untrusted_author`.

An author is trusted when GitHub lists the association as `OWNER` or `MEMBER`. Those are the defaults for `trusted-associations`. An account that joins the organization is trusted on the next read, with no config change, so review this list when membership changes. To drop `MEMBER`, set the list to `OWNER` only. An empty list is a configuration error.

`trusted-authors` adds named logins. `codeowners-file` adds the users it lists as `@user`. Team entries in that file count only when `resolve-teams` is true. A bot is trusted only when `trusted-apps` lists its login.

Outsider comments are dropped by default, because `untrusted-comments` defaults to `ignore`. With `data-only`, they reach the agent. Each one is marked as untrusted data, and the file tells the agent not to follow it.

The agent reads issue data only from its task file. Argus writes that file as a snapshot when it makes the claim, and the file does not change during the run. The example stage tells the agent to read issue data only from the task file.

## Claims

Argus picks an issue and reserves it on the board. After the reservation, Argus adds the claim label to the issue on GitHub. The label name is set by `issues.claim-label`, and its default is `accepted`. This is a GitHub label. It is not the board status named `accepted`.

If the repository has no label with that name, Argus creates it once. Argus never removes the label. If the label write fails, Argus logs the failure and keeps the claim, because the board reservation is the claim.

Argus skips any issue that carries the claim label, and counts it as `claim_label`. The name match ignores case. A person adds the label to keep Argus off an issue.

The board decides when it holds a claim on the issue. A claim held by this machine resumes, even with the label present. A claim held by another machine is skipped. An expired claim can be claimed again once its cooldown passes, with or without the label.

If two machines try to claim one issue at the same time, the board grants one claim. The other machine loses that race and skips the issue. Its attempt is lost work, and it starts no run.

## Failed runs

When an issue run fails, or uses up its remedy rounds, Argus gives back its board claim and keeps the label. The Needs You notice says to remove the label to retry.

Removing the label lets a later tick claim the issue again. While the label is there, it keeps the issue out of the pick. If the board still holds the claim, because the release failed or the run stopped first, the notice says so and does not ask for the label. Once that claim expires and its cooldown passes, a later tick takes the issue again, even with the label present.

An issue whose board record is completed, rejected, or accepted stays blocked.

## Launch faults

A launch fault is an issue run that cannot start. It happens when the `pre_run` hook refuses the run, or when Argus cannot cut the issue branch. A failure inside a stage is not a launch fault. It follows the failed-run path above.

Argus keeps the claim on that issue, and it starts no other issue. It tries the same issue again 10 minutes after the first failure, and 20 minutes after each later failure. After the third failure, Argus sends one Needs You notice that names the cause. Fix the cause on the host. The next retry starts the run, and the line resumes with no other action. If the claim is given back by any path, the count starts again.

Argus never starts an issue run on the base branch.

## Branches and publication

Each issue gets its own branch, named `issue/<number>-<first three title words>`. For example, issue 266 with the title "Backup restore refuses a stale board" gets `issue/266-backup-restore-refuses`. A title with no letters or digits gives `issue/<number>-task`. An issue already in progress keeps the branch that its run folder records.

The publication worker makes a minimal first commit and opens the draft pull request early. The `post_step` hook then reuses that draft and reads its draft state back. The hook runs `gitsync --kind issue --mode checkpoint`, which commits leftovers as a `wip:` commit and pushes them. Then it runs `gitsync --kind issue --mode open-pr --body-file FILE`. Both modes first check that this machine still holds the claim.

Argus does not merge pull requests. A person merges them.

## CI repair

A red check on an open issue pull request gets a CI repair round before new work, as a phase pull request does. Only the branch that an issue run folder records is read, and only a pull request from this repository counts. A fork pull request with the same branch name is ignored. See [CI repair rounds](running.md#ci-repair-rounds).

## Ready sweep

Each tick, the driver reads the same open issue pull requests as the CI repair step. It marks one ready for review only when every check that GitHub reports for it reads pass, and there is at least one check. A pending, failing, or missing check leaves it in draft, and the next tick asks again. The ready transition uses GitHub's ready/draft history as its once-only guard, so a pull request that was already readied, or that a person put back in draft, is not readied again. The sweep skips a pull request while its issue run or a repair round is running. It logs one line when it readies a pull request. Argus never merges it; a person does.

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

## Known gaps

These gaps are open in the Argus repository.

- A pending limit for CI checks, based on measured CI durations, is not built yet ([#197](https://github.com/agentmemoir/argus/issues/197)).
- Some host setup steps still run by hand ([#216](https://github.com/agentmemoir/argus/issues/216)).
- `argus --check` does not run the `pre_run` hook. A refusal from that hook appears only when a tick launches the run ([#220](https://github.com/agentmemoir/argus/issues/220)).
