# Coordinator loop (detail)

Evidence: Orca 1.4.223 spike run outside an Orca terminal (reused Claude worker and one owned `--agent claude` worker). Version-matched upstream docs: `ORCA skills get orchestration --reference references/coordinator-loop.md`, `messaging-and-gates.md`, `recovery-and-cleanup.md`. `ORCA` is the resolved executable (see SKILL.md section 0).

## Per-wave procedure

0. The ask rule lives in the worker contract (`<pane>/.orca/worker-contract.md`): unclear point or uncovered decision -> `orca orchestration ask` and wait; never guess, never open a local question dialog. The coordinator must then stay in `check --wait` so the answer arrives; a worker inside `ask` blocks until `reply` or its timeout.
1. `ORCA terminal list --json | python3 scripts/pane-roster.py --pane "<pane>" --self $SELF` -> pick eligible terminals.
2. For each Task: `ORCA terminal show --terminal <h> --json` (agentIdentity present) and `ORCA terminal wait --terminal <h> --for tui-idle --timeout-ms 120000`.
3. Default, one line per Task: `bash $SKILL/scripts/dispatch.sh --from $SELF --to <h> --title "<title>" --spec-file <spec.md> --goal "<original goal>" --change "<1-2 sentences>" --ownership "<paths>" --out-of-scope "<short>" --acceptance "<cmd>"... --protected <glob>... [--deps '<json>'] [--wave N] [--attempt N]`. It does `task-create` -> `init-checklist.py` (Done-when items from the spec, 1-8 or it refuses; prints `specHash`, writes `<state dir>/specs/<task>.json`) -> `install-worker-contract.sh` (no-op when current) -> `dispatch` (no `--inject`) -> short brief below -> `terminal send` -> tab rename -> `dispatches[]` entry with `spec_hash`. Alternative with Orca's own preamble and supervision: `ORCA orchestration worker-start --task <task_id> --terminal <h> --worktree path:"<pane>" [--from $SELF]` (run `init-checklist.py` yourself and store its `specHash` in the entry). `--model/--effort` cannot be combined with `--terminal`; a reused worker keeps its own model.
   Wave rule <!-- anti-skip:parallel-wave -->: dispatch Tasks in parallel only when their Ownership and Protected paths do not overlap; otherwise chain them with `--deps`. `--protected-diff` reads the whole worktree, so concurrent Tasks with overlapping paths fail each other's gate falsely.
4. Check `state`, `stage`, `failedStage`, `lastError`; exit code 1 = failure even when `ok:true`.
5. `ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 540000 --terminal $SELF` in a background shell command; a foreground command is killed at 600 s. `--types` is only the wake condition; the Delivery is the whole FIFO batch. Loop until every entry in the state file's `dispatches[]` is settled.
6. Handle each message, then `ORCA orchestration check --ack <delivery_id> --wait ... --terminal $SELF` (ack plus next batch in one call).
7. `bash <state dir>/gate/<entry gate_hash>/settle.sh --from $SELF --task <task_id> --rename-back` (frozen copy; path printed by `dispatch.sh` as `settle`) (gate with the stored `spec_hash`; never a bare `verify-checklist.py` without `--expect-spec-hash`), review the diff, then complete the accounting below.

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

Every Claude worker gets `/autocompact 300k` once (step 9 of `on`, or before its first brief when it joined later); handles are listed in the state file's `autocompact[]`. The contract's Context section tells it to write `plans/handoffs/session-handoff-<handle>.md` (with `ak:handoff`, `ce-handoff`, or by hand, per the skill family) before any `/compact`.

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
<!-- worker-contract:brief -->

Two parts. (1) The shared worker contract `<pane>/.orca/worker-contract.md`, installed once per pane by `scripts/install-worker-contract.sh` (SKILL.md `on` step 8b): reply/ask/heartbeat/escalation commands, completion rules, start ritual, mark-item/write-report usage, and the Method/Context lines for `skill_family`. A worker reads it once per session. (2) The per-dispatch brief below: only the values that change per Task (about 12 lines). Do not paste contract rules, the Method/Context lines or the ask rule into it.

Fill every placeholder; `$W` is the worker's own handle, `$C` the coordinator's. `$SKILL` is the absolute path of this skill directory, `$CHECKLIST` and `$REPORT` the absolute paths written by `init-checklist.py` before dispatch, `$SPEC_HASH` the specHash it printed (`dispatch.sh` fills all of these) (all agents: claude, grok, codex, agy; no hook needed). <!-- anti-skip:multi-agent:brief --> Verified on orca 1.4.223: a context-only Dispatch settles on `worker_done` exactly like an injected one (Task -> `completed`, worker row -> `unsupervised`/`retained`).

```
Read once if new: .orca/worker-contract.md
Coordinator: $C | Task: $TASK | Dispatch: $DISPATCH | You: $W
Checklist: $CHECKLIST
Report: $REPORT
Skill: $SKILL
Finish ONLY: bash $SKILL/scripts/orca-done.sh --from $W --task $TASK --dispatch $DISPATCH --checklist $CHECKLIST --report $REPORT --expect-spec-hash $SPEC_HASH --summary "…"
=== TASK ===
Goal: <one sentence original goal>
Change: <1–2 sentences>
Ownership: <paths>
Out of scope: <short>
Acceptance: <one command>
Done-when: see checklist (mark with: python3 $SKILL/scripts/mark-item.py --checklist $CHECKLIST --id N --evidence "…" or --run "<cmd>")
```

Notes:
- The full spec (In scope, Constraints, Protected paths, every acceptance command, numbered Done-when) lives in the Orca Task (`task-create --spec`) and in `$CHECKLIST` (`protectedPaths`, `acceptanceCommands`, `items`); the brief carries only the one-line view. The gate enforces the checklist regardless of brief length.
- Retry reason ("Previous attempt failed: …"), a domain skill ("also load `<skill>` first"), the branch, a no-commit instruction or the performance constraint go into `Change:` in a few words; never re-add the contract text.
- Raw `worker_done` stays an ask-first fallback: the contract tells the worker to use it only when `orca-done.sh` is missing in its terminal and you answered its `ask` saying so.
- Before every send, make sure `<pane>/.orca/worker-contract.md` exists (rerun `install-worker-contract.sh`; it is a no-op when current). A worker that has never seen the file (new terminal, or after `/clear`) reads it because of the first line.
- On Linux replace `orca` with the resolved executable in the contract (`--orca <exe>` on install).

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

Verify rule: the `worker_done` body (built by `orca-done.sh` from the checklist, not by the brief) must mention every number. Silence on an item = miss. Tests green alone do not clear unchecked items (e.g. commit, review skill, perf line).

<!-- anti-skip:multi-agent:donewhen -->
Checklist file and gate (all agents). The Done-when list also lives in `<pane>/.orca/tasks/<taskId>/checklist.json` (`init-checklist.py`), the worker marks items with `mark-item.py`, and it completes only through `orca-done.sh`, which runs `verify-checklist.py` and sends `worker_done` with the DONE-WHEN lines built from the file. The coordinator keeps the printed `specHash` in its state file and settles with `settle.sh` (`--expect-spec-hash`), because the checklist itself is worker-writable. Claude, Grok, Codex and agy follow the same path; a Claude Stop hook (`claude-stop-hook.example.json`) is an optional extra. A `worker_done` body without `DONE-WHEN:` and `gate:` lines was sent raw: run the gate yourself and treat a failure as the first failed attempt. Multi-step or risky Tasks get a `verify-<title>` Task (below). Full contract: `references/multi-agent-anti-skip.md`.

## Verify Task

Use after an implement Task with more than ~3 Done-when items, a change touching shared code, tests or data, or any worker that has overstated before.

1. `task-create --task-title "verify-<title>" --deps '["<implement task id>"]'` with spec: Original goal; "Check the tree yourself; do not trust the implement worker's worker_done body or report; do not fix anything"; the implement checklist path; Ownership and Protected paths of the implement Task; Done-when: (1) `python3 $SKILL/scripts/verify-checklist.py --checklist <implement checklist> --expect-spec-hash <implement spec_hash from the state file> --protected-diff --rerun-acceptance --cwd <pane>` exits 0, (2) diff (`git show --stat <hash>`, `git diff <baseRef>`) stays inside Ownership and In scope, (3) every implement Done-when item matches the code with file:line, (4) no test was skipped, deleted or weakened, (5) findings written to its own report.
2. Dispatch it with `dispatch.sh` like any Task (its own checklist and `spec_hash`).
3. Dispatch it when the implement Task settles, to a different idle terminal than the implement worker when one exists (any agent type); same terminal only after `/clear` or equivalent.
4. It completes through `orca-done.sh` like any Task. A finding = the implement Task failed verification: retry it once with "Previous attempt missed: <findings>".

## Method and Context lines per skill family

`on` records the skill family in the state file (`skill_family`: `ak`, `ce`, `superpowers` or `none`; detected from the orchestrator's own skills directory and plugin cache, overridable with `--skills`). `install-worker-contract.sh` copies the family's two lines from this table into the Method and Context sections of `<pane>/.orca/worker-contract.md` (parsed from this table; keep its row format). They are not repeated in per-dispatch briefs. The script appends to every Method line: "If a named skill is not installed in this terminal, say so in your worker_done summary and work without it."

| Family | Method line | Context line |
|--------|-------------|--------------|
| `ak` (AgentKit) | Invoke the named skill as your first step and follow it to the end; it carries the user's rules (brainstorm gate, tests, review). Implementation: `/ak:cook "<task title>"`. Bug, failing test or CI error: `/ak:debug "<symptom>"` to prove the cause, then `/ak:fix`. Before worker_done: `/ak:code-review` on your diff and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ak:handoff "task $TASK" --output plans/handoffs/session-handoff-$W.md --force`, then `/compact`, then continue from that file. |
| `ce` (compound-engineering) | Invoke the named skill as your first step and follow it to the end. Implementation: `/ce-plan "<task title>: <one-line change>"` then `/ce-work <plan path it wrote>`; for a change under about 20 lines `/ce-work "<task title>"` directly. Bug, failing test or CI error: `/ce-debug "<symptom>"` then `/ce-work` on its fix. Before worker_done: `/ce-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ce-handoff create "task $TASK"` and copy its output to plans/handoffs/session-handoff-$W.md, then `/compact`, then continue from that file. |
| `superpowers` | Use the superpowers skills; the brief's TASK block and checklist are the approved design, so skip brainstorming. Implementation: `writing-plans` then `executing-plans`, with `test-driven-development` while coding. Bug, failing test or CI error: `systematic-debugging` first. Before worker_done: `requesting-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first write plans/handoffs/session-handoff-$W.md (task, what is done, what is next, open questions), then `/compact`, then continue from that file. |
| `none` | Work directly: read the code around the target, make the change, run the brief's Acceptance command, and review your own diff before worker_done. | Same as `superpowers`. |

A domain skill named in the spec (for example `/ak:frontend-development`, `/ce-test-browser`) goes into the brief's `Change:` line as "also load <skill> first".

On Linux pass `--orca <resolved executable>` (for example `orca-ide`) to `install-worker-contract.sh`.

## What workers see (verbatim excerpt of the injected preamble)

This is what `worker-start` or `dispatch --inject` types into the worker's prompt (about 5 KB); the worker contract (once per pane) plus the short brief above replace it on the default path. Values in angle brackets are filled with real ones.

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
