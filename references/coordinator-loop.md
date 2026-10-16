# Coordinator loop (detail)

Evidence: Orca 1.4.223 spike run outside an Orca terminal (reused Claude worker and one owned `--agent claude` worker). Version-matched upstream docs: `ORCA skills get orchestration --reference references/coordinator-loop.md`, `messaging-and-gates.md`, `recovery-and-cleanup.md`. `ORCA` is the resolved executable (see SKILL.md section 0).

## Per-wave procedure

0. Every spec ends with the ask rule: unclear point or uncovered decision -> `orca orchestration ask` (from the preamble) and wait; never guess, never open a local question dialog. The coordinator must then stay in `check --wait` so the answer arrives; a worker inside `ask` blocks until `reply` or its timeout.
1. `ORCA terminal list --json | python3 scripts/pane-roster.py --pane "<pane>" --self $SELF` -> pick eligible terminals.
2. For each Task: `ORCA terminal show --terminal <h> --json` (agentIdentity present) and `ORCA terminal wait --terminal <h> --for tui-idle --timeout-ms 120000`.
3. Default: `ORCA orchestration task-create --spec "<spec>" --task-title "<title>" [--from $SELF]` -> `ORCA orchestration dispatch --task <task_id> --to <h> [--from $SELF]` (no `--inject`; read `result.dispatch.id`) -> write the compact brief below to a scratch file -> `ORCA terminal send --terminal <h> --text "$(cat brief)" --enter --wait-submit 15 --json`. Alternative with Orca's own preamble and supervision: `ORCA orchestration worker-start --task <task_id> --terminal <h> --worktree path:"<pane>" [--from $SELF]`. `--model/--effort` cannot be combined with `--terminal`; a reused worker keeps its own model.
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

Every Claude worker gets `/autocompact 300k` once (step 7 of `on`, or before its first brief when it joined later); handles are listed in the state file's `autocompact[]`. The brief tells it to write `plans/handoffs/session-handoff-<handle>.md` with `ak:handoff` before any `/compact`.

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

Fill every placeholder; `$W` is the worker's own handle, `$C` the coordinator's. Verified on orca 1.4.223: a context-only Dispatch settles on `worker_done` exactly like an injected one (Task -> `completed`, worker row -> `unsupervised`/`retained`).

```
You are a dispatched Orca worker. Coordinator terminal: $C. Task: $TASK. Dispatch: $DISPATCH.
Report ONLY through these commands (the coordinator cannot see this screen):
- Done (exactly once, required): orca orchestration send --from $W --type worker_done --subject "<short status>" --body "<3-sentence summary>" --task-id $TASK --dispatch-id $DISPATCH --outcome succeeded   (use --outcome failed if not done)
- Question (blocks until answered): orca orchestration ask --from $W --question "<question>" --timeout-ms 1800000
- Coordinator follow-ups: orca orchestration check --terminal $W --json
- Heartbeat every ~5 min while working: orca orchestration send --from $W --type heartbeat --subject alive --task-id $TASK --dispatch-id $DISPATCH --phase "<investigating|implementing|reviewing>"
If anything is unclear or a decision is not covered below, ask with the command above and wait; do not guess and do not open a local question dialog.
- Method: invoke the named skill as your first step and follow it to the end; it carries the user's rules (brainstorm gate, tests, review). Implementation: /ak:cook "<task title>". Bug, failing test or CI error: /ak:debug "<symptom>" to prove the cause, then /ak:fix. Before worker_done: /ak:code-review on your diff and fix what it finds. If the skill is not installed in this terminal, say so in your worker_done body and work without it.
- Context: your auto-compact window is 300k. When your status line shows Context at 70% or more, first run /ak:handoff "task $TASK" --output plans/handoffs/session-handoff-$W.md --force, then /compact, then continue from that file.
=== TASK ===
<the self-contained spec: Target, Change, Constraints, Ownership, Observable acceptance, git rules>
```

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
