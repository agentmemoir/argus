# Argus Philosophy Evaluation

**What this is.** Twenty scenarios that test whether a behaviour follows `PHILOSOPHY.md`. Run them by hand now; wire them into CI later.

**How to score.** Run the scenario. Compare the observable result with the expected line. A pass meets the expected result and breaks no belief. A fail names the belief number it broke.

| # | Scenario | Expected result | Belief |
|---|---|---|---|
| 1 | Fresh machine, no config file | Starts with defaults and prints the choices it made | 6 |
| 2 | Config file with one bad key | Refuses before work, names the key and the fix | 10 |
| 3 | Process killed during a phase | Next tick resumes the phase with no hand cleanup | 3, 11 |
| 4 | Stale lock left by a dead publish | Argus clears it, records the repair, continues | 3 |
| 5 | Test fails on every attempt | Three attempts, then Needs You with the cause and the count | 3, 8 |
| 6 | Coverage check did not run | Report says unknown, never passed or done | 4 |
| 7 | Model returns an empty plan | Retries within the budget, then Needs You with the cause | 3, 4 |
| 8 | Two phases change the same lines | Argus resolves it or hands it to the agents; no operator needed | 3 |
| 9 | Two phases state opposite product rules | Needs You names both rules and asks for the intent | 4, 5 |
| 10 | Operator stops the line | Needs You carries the resume command | 8 |
| 11 | Phase is queued behind another | Waiting; no message | 8 |
| 12 | Phase is claimed by another machine | Waiting; no message | 8 |
| 13 | Helper clears a lock a different way | Next tick re-checks and continues | 12 |
| 14 | Helper deletes the phase folder | Stops and records it; a restored or new folder is accepted next tick | 12 |
| 15 | Unknown file appears in a phase folder | Leaves it, finishes the phase, reports it in the morning | 3, 9 |
| 16 | Message from a sender Argus cannot verify | No inbound path; host access governs, no channel adds power | 12 |
| 17 | Helper proposes rewriting pushed history | Names the action, the reason, and the backup before it runs | 12, 2 |
| 18 | Upgrade meets an old config file | Runs with the old keys, warns, and needs no hand edit | 6, 11 |
| 19 | A week with no operator attention | No ritual, no cleanup command; sweeps and recovers alone | 11 |
| 20 | Agent proposes a behaviour change | Names the belief number in the commit or pull request | How to use |

**Status.** This is a manual rubric. It is not wired to CI, and no run has been recorded yet. The first recorded run becomes the baseline.

**A fail is information.** Fix the behaviour or change the belief through the recorded decision in `PHILOSOPHY.md`. Do not quietly edit the expected result.

Next: `PHILOSOPHY.md` for the beliefs, and `docs/running.md` for the operator path.
