# The Argus Philosophy

**Version 1.0.** 2026-10-19.

**What this is.** The beliefs that decide how Argus behaves. It is not a plan or a style guide. It holds the rules that every plan must obey. It is the source every contributor reads before changing what Argus does.

**Who reads it.** Anyone who designs, writes, reviews, or operates Argus, whether a person or an agent. Read it before you change Argus, and quote the belief you followed.

## The parts, and who decides

**The operator** is whoever answers the message. It makes no difference whether that is a person or an agent. One surface serves both. The operator owns the work and the limits.

**Argus** is the driver. It owns the loop: it picks the next item, holds the claim, watches the run, recovers a fault, publishes the result, and tells the operator. Argus is code. Its decisions are predictable and repeatable.

**glide** is the engine. It runs one pipeline and reports signals through hooks. It knows nothing about the operator's roadmap, claims, or publication.

**The agents inside a phase** do the work. They read, write, test, and explain. They are not trusted to decide whether the work is done, whether a check passed, or whether the run continues.

The rule is short: **the control plane is code, and the work is a model.** Never swap them.

When this document says "Argus", it means the driver. When it says "the system", it means Argus, glide, and the pipeline together, which is what the operator experiences.

Five words recur. A *phase* is one item of work. A *claim* is a lease on one phase, so two machines never run it twice. A *tick* is one scheduled pass of the driver. A *contributor* changes Argus: a person or an agent. A *helper* answers a Needs You message.

## 1. The system is a colleague on the night shift

You hand over the work. It carries the work.

The colleague is the whole system, not one model inside it. Argus is its steady hands, and it keeps the promise while the work changes from run to run.

A colleague is not a tool you drive, and not a servant you watch. You give the work and the limits. The system decides how to do the work. It works while you sleep. It speaks when it matters, and stays quiet when it does not.

The night shift is the promise: leave in the evening, find the work ready in the morning.

This belief settles tone. A message does not flatter. The driver does not ask for permission it does not need. The system does not make the operator supervise.

## 2. The operator's time is the first cost

Every minute spent configuring, watching, or rescuing Argus is a minute Argus failed to save.

This belief outranks elegance, cleverness, and feature count. When a design saves the operator one step and costs the code one ugly branch, take the branch.

This forbids:

- a setup step that could have a default
- a stop that a machine could have cleared
- a message that needs a log to understand

## 3. Recover first, report second

A fault is not news until Argus cannot fix it.

Argus expects a messy world: a half-written file, an old lock, a machine that restarted, a model that answered badly. These are normal. Recovery is the normal path.

Take one stale lock, left by a publish that died, written four wrong ways and one right way.

- **Report instead of retry:** "Phase 12 is blocked: a lock from phase 11 is still held. Clear it and retry." Argus could clear it alone.
- **Ask instead of act:** "A lock from phase 11 is still held. Should Argus clear it?" The operator must answer a question about Argus's own mess.
- **Repair in silence:** no message and no record. The run moves on, and the operator later finds a lock released and cannot tell who released it.
- **Retry without a limit:** the same attempt every ten seconds for six hours. By morning the log holds 2,000 identical lines, and the phase never moved.

Argus gives one fault three attempts: now, after 10 minutes, and after 20. Then it raises Needs You with the cause and the attempts it made.

A text conflict is work, not a decision. Argus resolves it or hands it to the agents inside the phase. A conflict of intent is a decision, and it goes to the operator.

The good line records the repair after the fact, and asks for nothing:

> "Phase 12 hit a stale lock from phase 11. Argus cleared it and resumed at 02:14. No action needed."

A fault is done when the operator needs to do nothing, and can still see what happened.

This forbids: handing the operator a problem a machine can solve.

## 4. Never buy quiet with a wrong action

The worst failure is a quiet wrong action.

Smoothness is never worth a false success. A green line that hides a red check is worse than a red line.

When the state is unknown, Argus says so. When it cannot check, it does not claim.

Honesty is the one quality that never bends for friendliness.

Take one skipped coverage check, reported four wrong ways and one right way.

- **False green:** "Phase 12 done." The coverage check never ran, and the line does not say so.
- **Warning in place of failure:** "One check failed, marked non-blocking." The operator reads a pass.
- **Confident guess:** "The coverage failure is unrelated." Argus has no evidence for that sentence.
- **Silent gap:** the skipped check appears as passed, and no line names it.

The good line is a list, not a mood:

> "Phase 12 is not done. Three of four checks ran. The coverage check did not run, so its result is unknown. It runs again at 02:20. No action needed."

Honesty is the list: what ran, what did not, and what is unknown.

A claim is done when a reader can tell those three things without opening a log.

This forbids: a false green, a warning in place of a failure, a confident guess, and a gap with no name.

## 5. The operator is never made to feel stupid

A failure is a fact to examine, not a fault to assign.

The message names what happened, why, and the one action to take. It does not blame. It does not scold. It does not repeat. It does not apologize in place of a fix.

Blame is not information. The operator and the system share the work, so the message carries only what helps the next step.

Take two phases that disagree about the product, written four bad ways and one good one. Phase 11 removed the retry cap, and phase 12 adds one back.

- **Blame:** "Phase 12 failed: conflicting requirements. Resolve them and retry." The line makes the conflict the operator's fault, and never says which rule.
- **Scold:** "Argus needs one source of truth before it can continue." A lecture. The operator already knows the rule; they need the way out.
- **Repeat:** the same line on every tick. By morning there are 38 copies, and the first one is lost.
- **Apologize:** "Sorry, something went wrong. Please try again." Polite, empty, and it hands the work back.

The good line names the cause, says what Argus will not decide, and gives one command:

> "Phase 12 is waiting: phase 11 removed the retry cap, and phase 12 adds one back. Argus will not choose your intent. Action: pick one, then run `argus --resume 12`."

A message is done when a tired operator knows what to type after one read.

The operator should feel helped, not judged.

This forbids: a message that explains the code instead of the situation, and a second notice for the same cause.

## 6. Defaults carry the weight; knobs carry the exception

Versatility is one product that fits many places, not many options.

Argus has a good default for every common case. A knob exists only when a real user needs it and can name it.

Take a first run on a new machine, designed four wrong ways and one right way.

- **Required knob:** "Set the repo root, the branch prefix, the pipeline path, and the token variable before the first run." That is homework, not setup.
- **Knob for a case nobody reported:** a setting for a host that never appeared. It still needs a doc line and a test.
- **Silent choice:** Argus picks a branch the operator did not expect, and never says which one.
- **Refuse without a default:** "No default for this case. Add config." A fresh machine cannot start.

The good line states the choices and asks for nothing:

> "No config found. Using the git root, branch `main`, and the `phases/` folder. Nothing to set."

A default is done when a fresh machine runs with no config, and the line says which choices Argus made.

This forbids: a required setting, and a knob added for a case nobody reported.

## 7. The operator owns the why; Argus owns the how

The operator decides what work matters.

Argus decides how the work moves: the order, the claim, the recovery, and the publication.

Take one empty queue, handled four wrong ways and one right way.

- **Invent work:** "No open phase, so Argus started one it found useful." Nobody asked for it.
- **Change the goal:** the phase asked for a fix. The fix was hard, so Argus shipped a smaller feature instead.
- **Reorder:** phase 11 is skipped because phase 12 looks more urgent.
- **Widen scope:** a refactor nobody asked for arrives inside the phase's pull request.

The good line stops at the edge of its authority:

> "No open phase. Argus is watching your list. Nothing new was started."

Argus stays inside its authority when every item it ran can be found in the operator's own list.

This forbids: deciding that a different task matters more, and skipping the goal because the path is easier.

## 8. Three states, and no fourth

**Working.** Argus is running the work, or repairing itself.

**Waiting.** The item is queued, waits on a dependency, or is claimed by another machine. No person is needed; the next tick moves it when it can.

**Needs You.** Argus cannot continue alone. This includes an item the operator stopped.

Take one missing token, shown four wrong ways and one right way.

- **Empty state:** a name like "paused" or "stuck" that says nothing about what comes next. The operator must ask someone to find out.
- **State without an action:** "Blocked." No cause, no next step.
- **State cleared by hand:** "Delete the marker file to continue." The recovery is a text edit.
- **Two names for one state:** "BLOCKED" in the log, "waiting for operator" on the board.

The good line is one state with one action:

> "Needs You. Phase 12 cannot reach the remote: no token is set. Action: set `GITHUB_TOKEN`, then run `argus --resume 12`."

A state is done when its name tells the operator what to do: nothing, or one action.

"Needs You" carries one cause and one action. The action may be one command, or an ordered pair such as fix, then resume.

Working and Waiting ask for nothing. A state that asks for action belongs in Needs You, and a state with no action and no progress is a defect.

## 9. Boring is a feature

The operator should be able to predict what Argus does.

Clever ideas belong inside the work. They do not belong in the contract.

Take the same phase, run four ways that surprise the operator, and one way that does not.

- **New strategy:** Argus picks a different branch shape because it looked better this time.
- **Clever heal:** an undocumented repair changes a file, and the next run inherits the change.
- **Hidden order:** items run in an order the operator cannot predict from the list.
- **Quiet contract change:** a minor upgrade changes the output shape, and a script breaks.

The good line lets the operator predict the run:

> "Phase 12 ran the same steps as phase 11, in your order, on the same branch shape. Nothing new is running."

Boring is done when the operator can describe the next run before it starts.

## 10. Checks move earlier; they do not move away

Friendliness is not a discount on quality.

Checks stay. Tests stay. Review stays. They move to the start, where they are cheap, instead of the middle, where they stop the line.

Take one bad config file, caught four wrong ways and one right way.

- **Check at the end:** the token is first used at push time, after the phase spent an hour on the work.
- **Check removed for speed:** the friendly version drops the gate and calls the phase done.
- **Vague refusal:** "Invalid config." It does not name the key.
- **Refusal with no path:** "Config error." It does not say what to change.

The good line refuses before the work starts, and names the way out:

> "Before the first tick: config is valid, the token works, and the branch is clean. Starting phase 12."

A check is in the right place when a bad setup costs seconds, not a night.

## 11. Low maintenance is the promise

A tool that needs care every week is a second job.

A fault costs the run minutes, not the operator's evening. Argus expects faults, recovers when recovery is possible, and stops cleanly when it is not.

Low maintenance has a shape: no weekly ritual, no hand migration after an upgrade, no cleanup command between runs, and no slow drift that only a person can see.

Take four maintenance burdens, and one promise kept.

- **Weekly ritual:** "Run the cleanup command every Sunday, or the board slows down." The operator must remember.
- **Hand migration:** "Edit the config key after each upgrade." An upgrade breaks an old file.
- **Drift:** the board and the branch disagree, and nothing reconciles them until a person looks.
- **Silent decay:** ticks slow and claims go stale, and no one is told until a run fails.

The good line is a month of quiet:

> "30 days, no hand. Argus swept 3 stale claims, re-pinned once, and recovered 2 faults. Nothing to run."

Maintenance is done when the operator can leave for a month, and no ritual is owed.

This forbids: a periodic chore, an upgrade that edits the operator's files, and a fault that waits for a person.

## 12. The helper is an operator

Argus does not care who answers the message. A person reads it. An agent parses it. Both get the same surface, and neither needs a special path.

The notice leaves Argus. A helper acts on the machine with the access it already has, and no channel adds power to that access.

The message carries three things: the cause, the one action with its command, and the check that proves it worked. A helper who receives it can close the loop without opening a log or asking a question.

Argus judges the result, not the route. A helper may fix the condition another way, or in part. The next tick re-checks the condition and continues when the result is safe: the condition is gone, no irreversible change happened, and the recorded state still matches. It does not require that its own command was the one that ran. A helper action that damages state stops the line.

Take four ways to waste a helper, and one way to accept help.

- **Log link:** "Phase 12 blocked. See the log." The helper must read a machine's diary.
- **One approved route:** the helper fixed the cause another way, and Argus rejects the fix because the command differed.
- **Second ritual:** the helper must also run a command to tell Argus the fix is done.
- **Hidden authority:** a destructive action happens, and no line names it, its reason, or its backup.

The good message is enough to act on, and enough to check:

> "Phase 12 is waiting: a stale lock from phase 11. Action: clear the lock. Check: no lock is left. The next tick resumes alone."

Help is accepted when any helper can act from the message alone, and Argus continues from the result.

A destructive action names itself, its reason, and its backup before it happens, in the record. The operator writes that line, and identity changes nothing: a person and an agent follow the same rule.

A restored phase folder, or a new one with different numbers, is accepted on the next tick. Argus records the change and carries on. It does not ask who put it there, and it needs no marker cleared.

This forbids: a message that needs a log, a fix rejected for taking a different route, and a destructive action with no record.

## Hard lines

These hold even when a belief above bends.

1. It never blames the operator, or makes them feel stupid.
2. It never takes an action it cannot undo without saying so first, and without a backup.
3. It never claims success it did not check.
4. It never leaves state that traps the next run.
5. It never requires a hand edit to recover.
6. It never publishes private data or host secrets.
7. It never decides what work is worth doing.
8. It never lets a model's judgement replace evidence about whether a check passed or whether work is done.
9. It never trusts a helper's claim without checking the condition.
10. It never needs a weekly chore to stay healthy.
11. It never makes an upgrade edit the operator's files.
12. It never grants a helper more power than the operator already has, whatever channel the message came through.

## What Argus is not

- Not a roadmap owner. The operator chooses the work.
- Not a CI system. It runs checks; it does not become one.
- Not a chat assistant. You give it work, not conversation.
- Not a framework to study. The first run should teach itself.
- Not a babysat service. A run that needs a viewer is not done.
- Not a maintenance job. A weekly ritual is a defect, not a feature.

## How to use this document

Before a design, a feature, or a behavior change:

1. Name the belief the change serves. Put its number in the commit or the pull request.
2. If the change breaks a belief, stop. Do not ship it.
3. When two beliefs pull apart, use this order:
   - **Honesty** first. A wrong action costs more than a slow one.
   - **The operator's time** next.
   - **Reversibility** next. Choose what can be undone.
   - **Simplicity** last.
4. At design time, when this document is silent, stop and ask the operator. At runtime, take the smallest action you can undo, record it, and raise Needs You only when no safe action exists. Do not invent a rule and call it philosophy.
5. When you think a belief is wrong, say so with evidence. The operator decides. Never edit this file to fit the code.

## How this document changes

This document carries a version and a date. A belief changes only through a recorded decision. The record names the belief, the evidence, and what changed. Between decisions, the code follows the document.

A change that weakens a belief needs the operator's explicit word. A definition or a worked example does not.

## The test

A design is Argus only if a tired operator, alone, on a fresh machine, can do these things, and so can an agent that receives the same message:

- start it without reading a manual
- leave for a night
- return to finished work, or to one message that says what to do next
- recover from any failure without a hand edit

If a design fails this test, it is not ready, however clever it is.

When the code and this document disagree, the code is the defect.

Next: `docs/running.md` for the operator path, and `docs/paths-and-ownership.md` for the state Argus owns.
