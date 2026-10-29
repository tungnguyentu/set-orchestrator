# Coordinator loop (detail)

Evidence: Orca 1.4.223 spike run outside an Orca terminal (reused Claude worker and one owned `--agent claude` worker). Version-matched upstream docs: `ORCA skills get orchestration --reference references/coordinator-loop.md`, `messaging-and-gates.md`, `recovery-and-cleanup.md`. `ORCA` is the resolved executable (see SKILL.md section 0).

## Per-wave procedure

0. Every spec ends with the ask rule: unclear point or uncovered decision -> `orca orchestration ask` (from the preamble) and wait; never guess, never open a local question dialog. The coordinator must then stay in `check --wait` so the answer arrives; a worker inside `ask` blocks until `reply` or its timeout.
1. `ORCA terminal list --json | python3 scripts/pane-roster.py --pane "<pane>" --self $SELF` -> pick eligible terminals.
2. For each Task: `ORCA terminal show --terminal <h> --json` (agentIdentity present) and `ORCA terminal wait --terminal <h> --for tui-idle --timeout-ms 120000`.
3. Default: `ORCA orchestration task-create --spec "<spec>" --task-title "<title>" [--from $SELF]` -> checklist: `cd <pane> && python3 $SKILL/scripts/init-checklist.py --task <task_id> --goal "<original goal>" --worker <h> --protected <glob>... --acceptance "<cmd>"... --item "<Done-when N>"...` (all agents; see `multi-agent-anti-skip.md`) -> `ORCA orchestration dispatch --task <task_id> --to <h> [--from $SELF]` (no `--inject`; read `result.dispatch.id`) -> write the compact brief below to a scratch file -> `ORCA terminal send --terminal <h> --text "$(cat brief)" --enter --wait-submit 15 --json`. Alternative with Orca's own preamble and supervision: `ORCA orchestration worker-start --task <task_id> --terminal <h> --worktree path:"<pane>" [--from $SELF]`. `--model/--effort` cannot be combined with `--terminal`; a reused worker keeps its own model.
4. Check `state`, `stage`, `failedStage`, `lastError`; exit code 1 = failure even when `ok:true`.
5. `ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 540000 --terminal $SELF` in a background shell command; a foreground command is killed at 600 s. `--types` is only the wake condition; the Delivery is the whole FIFO batch. Loop until every entry in the state file's `dispatches[]` is settled.
6. Handle each message, then `ORCA orchestration check --ack <delivery_id> --wait ... --terminal $SELF` (ack plus next batch in one call).
7. Verify the work, then complete the accounting below.

Delivery shape: `result` has `runId, deliveryId, messages[], count, replayed, acknowledged, timedOut, cancelled, connectionLost`. Message: `id, from_handle, to_handle, subject, body, type, payload (JSON string), created_at`.
- `question`: `from_handle=dispatch:<id>`, payload `{taskId, dispatchId, question, options}`; answer with `reply --id <msg.id>`.
- `worker_done`: `from_handle=<worker terminal>`, payload `{taskId, dispatchId, outcome}`. Confirm `dispatchId` is the awaited one and `outcome` is `succeeded` before accepting.
- Empty or timed-out `check --wait` is a checkpoint, not an error. After 3, run `worker-list --run <id> --json` and follow `projection.nextAction`.

## Completion accounting

After an accepted `worker_done`, do exactly one per Dispatch:

| Terminal kind | Facts | Action |
|---|---|---|
| Reused (`--terminal`, the user's agent) | `worker-list` shows `terminalState=retained`, `ownershipState=external`, `retainedReason=external_terminal`, `nextAction.kind=none`; never `reclaimable` | Reuse for the next Dispatch, or call `worker-release` just to record settlement: it returns `retained/external_terminal` and does NOT close the terminal |
| Owned (`--agent`, only on user request) | After `worker_done` it becomes `reclaimable`; `nextAction` argv is `worker-release --dispatch <id>` | `worker-release` closes it and keeps an archive (`worker-read` still works); or `worker-retain` to keep it live (a later release after retain is a no-op) |

Context-only Dispatches (default compact path) show `workerState=unsupervised`, `terminalState=retained`, liveness `unverifiable/unsupervised_settled` after `worker_done`; `worker-release` returns `retained/no_owned_resource`. Nothing to clean up.

Remaining debts: `ORCA orchestration worker-list --run <id> --terminal-state reclaimable --json`; do not finish your turn while it is non-empty. A failed Dispatch with no resource releases as `retained/no_owned_resource`. Settle (ack + release/retain) the old Dispatch before giving that terminal new work.

## Reused worker context

Every Claude worker gets `/autocompact 300k` once (step 7 of `on`, or before its first brief when it joined later); handles are listed in the state file's `autocompact[]`. The brief's Context line tells it to write `plans/handoffs/session-handoff-<handle>.md` (with `ak:handoff`, `ce-handoff`, or by hand, per the skill family) before any `/compact`.

A Claude Code worker that handles several Tasks in a row fills its context. Its status line in `terminal read` shows `Context ██░░░░░░░░ NN%`. Rule: before a new brief to a settled, idle terminal with NN above 70, send `/clear` and wait for `tui-idle`; then send the brief. Never `/clear` a terminal with an active Dispatch. For Codex, Grok or Gemini use their own reset command when known; otherwise skip and note it in the report.

## Failed Task retry

`worker-start --retry-of` is not available on the compact path, so retry by creating a new Task: same title plus " (retry)", same `--deps`, spec prefixed with the failure reason from the `worker_done` body. Record `attempt: 2` on the state-file entry. One retry only; a second failure marks the Task stopped, dependents stay blocked, independent Tasks continue.

## Error codes seen

| code / state | When | Response |
|---|---|---|
| `no_active_sender_terminal` | `reply`/`send`/`run-*` outside a terminal without `--from` | Add `--from $SELF` |
| `runtime_error` "already has an active dispatch" | `dispatch` to a terminal with an active Dispatch | Wait for its `worker_done` or choose another terminal; do not retry as is |
| `task_not_startable` | Task already `completed` (see `data.status`, `data.nextSteps`) | Create a new Task |
| `task_not_found` | Wrong Task id | `task-list` |
| `agent_unconfigured` | `worker-start --terminal` while the agent is running a command (identity vanishes) | `terminal wait --for tui-idle`, then retry |
| `state=failed`, `failedStage=agent_readiness` (exit 1, `ok:true`) | `worker-start --terminal` to a terminal with an active Dispatch | Read `lastError`; release the failed Dispatch; `task-update --status failed` if abandoning |
| `release_unknown` (exit 1) | Uncertain release | Follow its recovery receipt; never `terminal close` |

Not seen in the spike: `inject_rejected`, `release_pending`, `outcome_unknown`, `nested_worker_depth_exceeded`.

## Compact brief template (default dispatch path)

Fill every placeholder; `$W` is the worker's own handle, `$C` the coordinator's. `$SKILL` is the absolute path of this skill directory, `$CHECKLIST` and `$REPORT` the absolute paths written by `init-checklist.py` before dispatch (all agents: claude, grok, codex, agy; no hook needed). <!-- anti-skip:multi-agent:brief --> Verified on orca 1.4.223: a context-only Dispatch settles on `worker_done` exactly like an injected one (Task -> `completed`, worker row -> `unsupervised`/`retained`).

```
You are a dispatched Orca worker. Coordinator terminal: $C. Task: $TASK. Dispatch: $DISPATCH.
Report ONLY through these commands (the coordinator cannot see this screen):
- Checklist: $CHECKLIST   (= <pane>/.orca/tasks/$TASK/checklist.json; after each item: python3 $SKILL/scripts/mark-item.py --checklist $CHECKLIST --id <N> --evidence "<command -> result | commit | file:line>"; never edit step text)
- Report: $REPORT   (= <pane>/.orca/tasks/$TASK/report.json; python3 $SKILL/scripts/write-report.py --task $TASK --dispatch $DISPATCH --report $REPORT --step <N> --run "<command>")
- Start ritual: pwd; git status --short; git log --oneline -3; cat $CHECKLIST; run the first acceptance command once; heartbeat "step 0/N".
- Done (exactly once, required, the ONLY completion path): bash $SKILL/scripts/orca-done.sh --from $W --task $TASK --dispatch $DISPATCH --checklist $CHECKLIST --report $REPORT --subject "<short status>" --summary "<2 sentences>"   (it verifies the checklist, protected paths and acceptance, then sends worker_done; on "GATE FAILED" finish the missing items and rerun; if you cannot finish, rerun with --outcome failed and say what is not done)
- Only if orca-done.sh is missing in this terminal: ask first; then orca orchestration send --from $W --type worker_done --subject "<short status>" --body "<DONE-WHEN: for each numbered item `N. done: <evidence>` or `N. skipped: <reason>`; then a 2-sentence summary>" --task-id $TASK --dispatch-id $DISPATCH --outcome succeeded   (use --outcome failed if any Done-when item is not done)
- Question (blocks until answered): orca orchestration ask --from $W --question "<question>" --timeout-ms 1800000
- Coordinator follow-ups: orca orchestration check --terminal $W --json
- Heartbeat every ~5 min while working: orca orchestration send --from $W --type heartbeat --subject "step <k>/<N>: <item, short>" --task-id $TASK --dispatch-id $DISPATCH --phase "<investigating|implementing|reviewing>"
If anything is unclear or a decision is not covered below, ask with the command above and wait; do not guess and do not open a local question dialog.
- Method: <Method line for the skill family, table below>
- Context: <Context line for the skill family, table below>
=== TASK ===
<the self-contained spec: Original goal, In scope, Out of scope, Target, Change, Constraints, Ownership, Protected paths (never edit, skip or weaken tests/fixtures/snapshots/CI to pass), Observable acceptance, Done-when checklist (numbered, identical to $CHECKLIST), git rules, worker_done evidence rule, ask rule>
```

## Performance checklist

The orchestrator puts this list in the Constraints of every Task that touches UI, API, data access or static assets, and reads every diff against it in verify step 5. The worker reports, per item, what it checked or that the item does not apply.

1. Unnecessary re-renders: no new render on unchanged props or state; memoize or split components where the change would otherwise re-render a subtree on every update.
2. Bundle size: new routes, heavy components and rarely used libraries are lazy-loaded; no new import that pulls a large dependency into the initial bundle.
3. Requests and caching: no duplicate or waterfall requests for the same data; responses that can be cached carry cache headers or go through the app's query cache.
4. Slow queries and N+1: every loop that queries per item is replaced by a batched or joined query; new queries use an existing index or add one.
5. Images, fonts and scripts: images sized and compressed with explicit dimensions, fonts subset or preloaded, third-party scripts deferred or async.

Brief line to add under `=== TASK ===` Constraints: "Performance: do not regress re-renders, bundle size, requests and caching, query count, or asset loading; in your worker_done body list each of the five with ok, fixed, or n/a."


## Done-when checklist (anti-skip)

Orchestrator writes the list; worker executes and reports; coordinator verifies.

Good items (observable):
- `1. Add failing test in path/to/file.test.ts for <behavior>`
- `2. Implement until that test passes: <command>`
- `3. Run <full acceptance command>; paste exit code`
- `4. Commit on branch <name> with message <pattern>; do not push`

Bad items (invite skipping):
- `Implement the feature`
- `Make sure everything works`
- `Follow best practices`

Split rule: if you need more than 8 items, create another Task with `--deps` on the first. One active concern per Task beats one long prompt the worker self-prioritizes.

Verify rule: body must mention every number. Silence on an item = miss. Tests green alone do not clear unchecked items (e.g. commit, review skill, perf line).

<!-- anti-skip:multi-agent:donewhen -->
Checklist file and gate (all agents). The Done-when list also lives in `<pane>/.orca/tasks/<taskId>/checklist.json` (`init-checklist.py`), the worker marks items with `mark-item.py`, and it completes only through `orca-done.sh`, which runs `verify-checklist.py` and sends `worker_done` with the DONE-WHEN lines built from the file. Claude, Grok, Codex and agy follow the same path; a Claude Stop hook (`claude-stop-hook.example.json`) is an optional extra. A `worker_done` body without `DONE-WHEN:` and `gate:` lines was sent raw: run the gate yourself and treat a failure as the first failed attempt. Multi-step or risky Tasks get a `verify-<title>` Task (below). Full contract: `references/multi-agent-anti-skip.md`.

## Verify Task

Use after an implement Task with more than ~3 Done-when items, a change touching shared code, tests or data, or any worker that has overstated before.

1. `task-create --task-title "verify-<title>" --deps '["<implement task id>"]'` with spec: Original goal; "Check the tree yourself; do not trust the implement worker's worker_done body or report; do not fix anything"; the implement checklist path; Ownership and Protected paths of the implement Task; Done-when: (1) `python3 $SKILL/scripts/verify-checklist.py --checklist <implement checklist> --protected-diff --rerun-acceptance --cwd <pane>` exits 0, (2) diff (`git show --stat <hash>`, `git diff <baseRef>`) stays inside Ownership and In scope, (3) every implement Done-when item matches the code with file:line, (4) no test was skipped, deleted or weakened, (5) findings written to its own report.
2. `init-checklist.py` for the verify Task (its own `<pane>/.orca/tasks/<verify id>/checklist.json`).
3. Dispatch it when the implement Task settles, to a different idle terminal than the implement worker when one exists (any agent type); same terminal only after `/clear` or equivalent.
4. It completes through `orca-done.sh` like any Task. A finding = the implement Task failed verification: retry it once with "Previous attempt missed: <findings>".

## Method and Context lines per skill family

`on` records the skill family in the state file (`skill_family`: `ak`, `ce`, `superpowers` or `none`; detected from the orchestrator's own skills directory and plugin cache, overridable with `--skills`). Fill the two brief lines from this table. Every Method line ends with: "If a named skill is not installed in this terminal, say so in your worker_done body and work without it."

| Family | Method line | Context line |
|--------|-------------|--------------|
| `ak` (AgentKit) | Invoke the named skill as your first step and follow it to the end; it carries the user's rules (brainstorm gate, tests, review). Implementation: `/ak:cook "<task title>"`. Bug, failing test or CI error: `/ak:debug "<symptom>"` to prove the cause, then `/ak:fix`. Before worker_done: `/ak:code-review` on your diff and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ak:handoff "task $TASK" --output plans/handoffs/session-handoff-$W.md --force`, then `/compact`, then continue from that file. |
| `ce` (compound-engineering) | Invoke the named skill as your first step and follow it to the end. Implementation: `/ce-plan "<task title>: <one-line change>"` then `/ce-work <plan path it wrote>`; for a change under about 20 lines `/ce-work "<task title>"` directly. Bug, failing test or CI error: `/ce-debug "<symptom>"` then `/ce-work` on its fix. Before worker_done: `/ce-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ce-handoff create "task $TASK"` and copy its output to plans/handoffs/session-handoff-$W.md, then `/compact`, then continue from that file. |
| `superpowers` | Use the superpowers skills; the spec below is the approved design, so skip brainstorming. Implementation: `writing-plans` then `executing-plans`, with `test-driven-development` while coding. Bug, failing test or CI error: `systematic-debugging` first. Before worker_done: `requesting-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first write plans/handoffs/session-handoff-$W.md (task, what is done, what is next, open questions), then `/compact`, then continue from that file. |
| `none` | Work directly: read the code around the target, make the change, run the acceptance commands below, and review your own diff before worker_done. | Same as `superpowers`. |

A domain skill named in the spec (for example `/ak:frontend-development`, `/ce-test-browser`) is appended to the Method line as "also load <skill> first".

On Linux replace `orca` with the resolved executable (for example `orca-ide`) inside the brief as well.

## What workers see (verbatim excerpt of the injected preamble)

This is what `worker-start` or `dispatch --inject` types into the worker's prompt (about 5 KB); the compact brief above replaces it on the default path. Values in angle brackets are filled with real ones.

```
You are working inside Orca, a multi-agent IDE. You are a dispatched worker.
Your coordinator's terminal handle is: <coordinator handle>
Your task ID is: <task id>

The coordinator cannot see this terminal, so reach it with the `orca orchestration`
commands below; a question or result left only in this terminal never gets to it.
Don't post to Slack, GitHub, or other channels during the run; report through these commands.

  # Report the task outcome (required, exactly once).
  orca orchestration send --from <worker_terminal_handle> --type worker_done --subject "<short status>" --body "<3-sentence summary>" --task-id <task_id> --dispatch-id <dispatch_id> --outcome succeeded
  # Heartbeat every 5 minutes while working
  orca orchestration send --from <worker_terminal_handle> --type heartbeat --subject "alive" --task-id <task_id> --dispatch-id <dispatch_id> --phase "<investigating|implementing|reviewing|waiting>"
  # Ask the coordinator and block until it answers (instead of AskUserQuestion)
  orca orchestration ask --from <worker_terminal_handle> --question "<question>" --options "<a,b>" --timeout-ms 600000
  # Escalate a blocker
  orca orchestration send --from <worker_terminal_handle> --type escalation --subject "Blocked: <reason>" --body "<details>" --task-id <task_id> --dispatch-id <dispatch_id>
  # Read coordinator follow-ups (nothing interrupts the worker; it checks at checkpoints and before worker_done)
  orca orchestration check --terminal <worker_terminal_handle> --json

=== AFTER YOU SEND worker_done ===
worker_done ends the turn: stop, return to an idle prompt, no further actions. A direct
user instruction takes precedence. The terminal stays open; a later Dispatch arrives as a
fresh preamble + TASK block.

=== TASK ===
<spec>
```

Consequences: the worker only reports if it follows this text (not verified for non-Claude/Codex agents); an empty or vague spec makes it `ask` you; a worker may be quiet while inside `ask` or `check --wait`.

## Fallback evidence rule

Leave a wait only on positive proof the agent stopped: `exited` liveness, a transcript (`worker-read --dispatch <id> --source transcript`) whose final agent turn sent no `worker_done`, or an arrived `FB-<nonce>-END` block. `unverifiable` liveness, empty waits, and a static tail are absence, which authorizes nothing: keep waiting or follow `projection.nextAction`.
