# Coordinator loop (detail)

Evidence: Orca 1.4.223 spike run outside an Orca terminal (reused Claude worker and one owned `--agent claude` worker). Version-matched upstream docs: `ORCA skills get orchestration --reference references/coordinator-loop.md`, `messaging-and-gates.md`, `recovery-and-cleanup.md`. `ORCA` is the resolved executable (see SKILL.md section 0).

## Per-wave procedure

0. The ask rule lives in the worker contract (`<pane>/.orca/worker-contract.md`): unclear point or uncovered decision -> `orca orchestration ask` and wait; never guess, never open a local question dialog. The coordinator must then stay in `check --wait` so the answer arrives; a worker inside `ask` blocks until `reply` or its timeout.
1. `ORCA terminal list --json | python3 scripts/pane-roster.py --pane "<pane>" --self $SELF` -> pick eligible terminals.
2. For each Task: `ORCA terminal show --terminal <h> --json` (agentIdentity present) and `ORCA terminal wait --terminal <h> --for tui-idle --timeout-ms 120000`.
3. Default, one line per Task: `bash $SKILL/scripts/dispatch.sh --from $SELF --to <h> --title "<title>" --spec-file <spec.md> --goal "<original goal>" --change "<1-2 sentences>" --ownership "<paths>" --out-of-scope "<short>" --acceptance "<cmd>"... --protected <glob>... --tier <light|standard|heavy|frontier> [--tier-override "<reason>"] [--frontier-accepted "<user's words>"] [--keep-context "<reason>"] [--max-per-context N] [--deps '<json>'] [--wave N] [--attempt N]`. Before anything is created it refuses a terminal that still has another `dispatched` or `active` Dispatch (exit 7, no override), refuses a Claude worker that already took 3 Tasks since its last context reset (exit 8; `--keep-context` or `fresh-worker.sh`), then checks the Task's tier against the worker's roster model (exit 5 on a mismatch unless `--tier-override`) and, for a frontier Task or a `fable` worker, the user's recorded acceptance (exit 6; never bypassed by the override). Then it does `task-create` -> `init-checklist.py` (Done-when items from the spec, 1-8 or it refuses; prints `specHash`, writes `<state dir>/specs/<task>.json`) -> `install-worker-contract.sh` (no-op when current) -> `dispatch` (no `--inject`) -> short brief below -> `terminal send` -> tab rename -> `dispatches[]` entry with `spec_hash`, `tier`, `model` and `effort`. Alternative with Orca's own preamble and supervision: `ORCA orchestration worker-start --task <task_id> --terminal <h> --worktree path:"<pane>" [--from $SELF]` (run `init-checklist.py` yourself and store its `specHash` in the entry). `--model/--effort` cannot be combined with `--terminal`; a reused worker keeps its own model.
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

Every Claude worker gets `/autocompact 300k` once (step 9 of `on` in `setup.md`, or before its first brief when it joined later); handles are listed in the state file's `autocompact[]`. The contract's Context section tells it to write `plans/handoffs/session-handoff-<handle>.md` (with `ak-handoff`, `ce-handoff`, or by hand, per the skill family) before any `/compact`.

A Claude Code worker that handles several Tasks in a row fills its context, and its answers get worse as it does. Two rules, both in code:

- `dispatch.sh` exits 8 for a Claude worker that already took 3 Tasks since its last context reset (`orch_state.py tasks-since-reset`; set the limit, from 1 to 6, with `--max-per-context N` or `ORCH_MAX_TASKS_PER_CONTEXT`). Run `bash $SKILL/scripts/fresh-worker.sh --terminal <h>` (it writes `context_resets[<handle>]` when done), then dispatch again. A Task that edits the same files as the previous one can pass with `--keep-context "<reason>"`; the reason is stored and goes into the final report. It is an exception, not a habit: "same routine" or "knows the code" is not a reason, because the handoff file carries that. From 6 Tasks since the last reset on, exit 8 has no override. The check never applies to Codex, Grok or Antigravity workers.
- Earlier than that: the status line in `terminal read` shows `Context ██░░░░░░░░ NN%`. When NN is above 70 before a new brief to a settled, idle terminal, run `fresh-worker.sh` without waiting for exit 8.

`fresh-worker.sh` has the worker write its handoff, confirms the file is newer than the request, then sends `/clear` and waits for `tui-idle`. The same script switches a worker's model for a Task's tier (`--model`, `--effort`). A `/clear` without a fresh handoff file is not allowed: the script exits 5 and sends nothing (steps in `model-routing.md`, "Handoff, then clear"). It refuses a terminal with an open Dispatch (exit 3). For Codex, Grok or Gemini use their own reset command when known; otherwise skip and note it in the report.

## Failed Task retry

`worker-start --retry-of` is not available on the compact path, so retry by creating a new Task: same title plus " (retry)", same `--deps`, spec prefixed with the failure reason from the `worker_done` body. Send the retry one rung up, never to the same model and effort: same model with a higher effort when such a worker is idle, otherwise the next tier (`model-routing.md`). Record `attempt: 2` on the state-file entry. One retry only; a second failure marks the Task stopped, dependents stay blocked, independent Tasks continue.

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

Two parts. (1) The shared worker contract `<pane>/.orca/worker-contract.md`, installed once per pane by `scripts/install-worker-contract.sh` (`setup.md`, `on` step 8b): reply/ask/heartbeat/escalation commands, completion rules, start ritual, mark-item/write-report usage, and the Method/Context lines for `skill_family`. A worker reads it once per session. (2) The per-dispatch brief below: only the values that change per Task (about 12 lines). Do not paste contract rules, the Method/Context lines or the ask rule into it.

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
| `ak` (AgentKit) | Invoke the named skill as your first step and follow it to the end; it carries the user's rules (brainstorm gate, tests, review). Implementation: `/ak-cook "<task title>"`. Bug, failing test or CI error: `/ak-debug "<symptom>"` to prove the cause, then `/ak-fix`. Before worker_done: `/ak-code-review` on your diff and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ak-handoff "task $TASK" --output plans/handoffs/session-handoff-$W.md --force`, then `/compact`, then continue from that file. |
| `ce` (compound-engineering) | Invoke the named skill as your first step and follow it to the end. Implementation: `/ce-plan "<task title>: <one-line change>"` then `/ce-work <plan path it wrote>`; for a change under about 20 lines `/ce-work "<task title>"` directly. Bug, failing test or CI error: `/ce-debug "<symptom>"` then `/ce-work` on its fix. Before worker_done: `/ce-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first run `/ce-handoff create "task $TASK"` and copy its output to plans/handoffs/session-handoff-$W.md, then `/compact`, then continue from that file. |
| `superpowers` | Use the superpowers skills; the brief's TASK block and checklist are the approved design, so skip brainstorming. Implementation: `writing-plans` then `executing-plans`, with `test-driven-development` while coding. Bug, failing test or CI error: `systematic-debugging` first. Before worker_done: `requesting-code-review` and fix what it finds. | Your auto-compact window is 300k. When your status line shows Context at 70% or more, first write plans/handoffs/session-handoff-$W.md (task, what is done, what is next, open questions), then `/compact`, then continue from that file. |
| `none` | Work directly: read the code around the target, make the change, run the brief's Acceptance command, and review your own diff before worker_done. | Same as `superpowers`. |

A domain skill named in the spec (for example `/ak-frontend-development`, `/ce-test-browser`) goes into the brief's `Change:` line as "also load <skill> first".

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

## Loop steps in detail (numbering matches SKILL.md)

The outline in SKILL.md keeps these step numbers. The command line and the order of what `dispatch.sh` does are in "Per-wave procedure" step 3 above; completion accounting, reused worker context, the retry order and the performance checklist have their own sections above and below.

**Step 1, Spec.** Per Task, write a self-contained spec: Target, Change, Tier (`light`, `standard`, `heavy` or `frontier`; the lowest that fits, table in `references/model-routing.md`), Constraints, Ownership (files it may edit), Observable acceptance (exact test and build commands), Skill (the worker contract's Method line for the state file's `skill_family`, not repeated per brief: `/ak-cook` or `/ak-debug` then `/ak-fix`; `/ce-plan` and `/ce-work` or `/ce-debug`; superpowers `writing-plans`, `executing-plans` or `systematic-debugging`; add a domain skill when one fits, for example `/ak-frontend-development` or `/ce-test-browser`, so the worker loads it first; on the compact path name it in the brief's `Change:` line); plus "commit (unless the user asked for no commit), do NOT push, do NOT deploy; do not `git add -A`; state the branch". Every Task that touches UI, API, data access or static assets also carries the performance checklist in its Constraints (list in section "Performance checklist" below): the worker must not add a regression on any item and must report what it checked. Done-when checklist (required): every Task spec includes a numbered Done-when list (3–8 items). Each item must be observable (file path, command, or UI check the coordinator can re-run). Never replace this with "implement X end-to-end" or a vague Method-only brief. If the user's request has more than ~8 observable steps, split into multiple Tasks with `--deps` instead of one mega-spec. The worker may not invent extra steps that skip a listed item. Evidence, the ask rule and completion are covered by the worker contract (`.orca/worker-contract.md`) and `orca-done.sh`, which builds the DONE-WHEN lines from the checklist; do not repeat them in the spec. A vague spec makes the worker `ask` instead of work.

Tier and the dispatch flags: give every Task a `Tier`; `dispatch.sh --tier <tier>` refuses (exit 5) a worker whose roster `model` maps to another tier, and `--tier-override "<reason>"` lets a mismatch through and records the reason as `tier_override`. A `frontier` Task, or any Task sent to a `fable` worker, also needs the user's yes for that Task: `--frontier-accepted "<their words>"` (exit 6 without it; `--tier-override` does not bypass it). Both rules and the model-to-tier table: `model-routing.md`. The script never classifies a Task; you pick the tier, the roster in the state file is the only source of a worker's model.

**Step 2, Pick terminals.** Re-run the roster. Match each Task's Tier to a worker's `model` in `roster[]`: `light` to a small model, `standard` to a mid model, `heavy` to the strongest open one. If no worker of that tier is idle, switch an idle Claude worker whose previous Dispatch is settled to the tier's model yourself: handoff, `/clear`, `/model <name>` (and `/effort <level>`), in that order (procedure "Handoff, then clear" in `references/model-routing.md`), then update its `roster[]` entry. Codex, Grok and Antigravity workers cannot be switched in place: use the nearest tier that is idle and note it for the report. Do not open a terminal without the user's request, but do tell the user which `--open <agent>@<model>` would fit. When the user asks for more agents mid-run ("mở thêm 1 codex"), run step 5 of `on` (`setup.md`) with `--next` set past the highest roster number, add the handles to `opened[]`, and continue with them as new terminals. Make sure `<pane>/.orca/worker-contract.md` exists (rerun step 8b of `on`; a no-op when current; once per pane, never per worker); a new worker reads it via the brief's first line. A Claude terminal that is not yet in the state file's `autocompact[]` list (opened after `on`) gets `/autocompact 300k` as in step 9 of `on` before its first brief. When the user names a worker by its tab title (for example "giao cho 2FA"), resolve it with `python3 scripts/pane-roster.py --pane "<pane path>" --self $SELF --title "<text>"`: case-insensitive substring match on the `title` column, status glyph ignored, same pane only. Exit 3 (no match) or 4 (several matches, listed on stderr): show the user the candidates and ask which one; never guess and never widen the search to other panes. Titles are yours to manage (step 10 of `on`): a terminal that joined after `on` gets `<agent>-<n>` with the next free number before its first brief. Before each start, `ORCA terminal show --terminal <h> --json` must show `agentIdentity`, and `ORCA terminal wait --terminal <h> --for tui-idle` must be satisfied (an agent mid-command yields `agent_unconfigured`). One active Dispatch per terminal (`dispatch.sh` exits 7 otherwise).

**Step 3, extras.** Glance once with `ORCA terminal read --terminal <h> --limit 40` after the send: an accepted send does not prove the agent began. Context-only Dispatches show as `unsupervised`/`retained` with no liveness: rely on heartbeats, `check --wait` and `terminal read`. Use `worker-start --task <id> --terminal <h> --worktree path:"<pane path>"` instead only when the user wants Orca's full preamble and supervision (then run `init-checklist.py` yourself and record `spec_hash`), and `worker-start --agent <id>` only when the user asks for a new terminal; read exit code and `result.state/failedStage/lastError`, not just `ok`. `dispatch.sh` refuses a spec with no Done-when list or more than 8 items; re-running it for the same `--task` reuses the checklist and dispatch; it prints one JSON line (`taskId, dispatchId, specHash, checklist, report, brief`); exit 1 means an Orca step failed (the entry's `status` says which).

**Step 4, Wait.** `ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 540000 --terminal $SELF`, run as a background shell command (a foreground command dies at 600 s). When it returns, process the whole Delivery before `--ack`: forward every `question` and `escalation` to the user one at a time, then `ORCA orchestration reply --id <msg_id> --body ... [--from $SELF]`; match `worker_done.payload.dispatchId` to the Dispatch you await and check `outcome`; then `check --ack <delivery_id> --wait ...` again in the background. An empty return is a checkpoint: loop until every dispatch in the state file is settled. Never end your turn while one is still active.

**Step 5, Verify** yourself, because an agent can overstate what it did: run the frozen `settle.sh` (Per-wave step 7), then `git log --oneline -3`, `git show --stat <hash>`, read the diff; check that it stays inside the spec's Ownership and that out-of-scope files were not damaged; check that reported test counts are plausible, and rerun the acceptance commands when in doubt. For a no-commit Task use `git status --short`, read the changed files and `worker_done.payload.filesModified`. Always run the frozen `settle.sh`, whatever the body says; a raw `worker_done` without the `DONE-WHEN:` / `gate:` lines, `gate: SKIPPED`, a specHash mismatch, a protected-path diff, a failed rerun, or evidence that does not hold (unknown commit, missing file:line) is a verification failure (first failed attempt). Plain-text evidence is only a warning (`unverifiedItems`): check those items yourself.

**Step 6, Account for completion** (reuse, retain, or release) per "Completion accounting"; reused terminals are never closed by release. Reused worker context: "Reused worker context" above. Performance checklist: read the diff against it (section below); a regression, or a worker_done body that does not say what it checked, is a verification issue: note it in the report with file:line and name the item in the retry spec. Items that the diff cannot affect (a backend-only change and re-renders, for example) are marked not applicable, not skipped silently.

Own context. At every wave boundary and after each `check --wait` return, `ORCA terminal read --terminal $SELF --limit 20` and read your own `Context ██░░ NN%` line (a chat session cannot read itself: use the context figure the harness shows you, or treat every third wave as the threshold). Above 70 %: update the state file, write `plans/handoffs/session-handoff.md` (relative to the pane path) in this turn: with `/ak-handoff "orchestrator <run_id>" --output plans/handoffs/session-handoff.md --force` when `skill_family` is `ak`, with `/ce-handoff create "orchestrator <run_id>"` copied to that path when it is `ce`, otherwise by hand (the continuation contract: Run, state file, active dispatches, open questions, next action), then queue two inputs to yourself with `ORCA terminal send --terminal $SELF --enter --text ...`: first `/compact keep: orchestrator role, run id, state file path, plans/handoffs/session-handoff.md, active dispatches and their terminals, forwarded questions`, then `/set-orchestrator status, then continue the coordinator loop`; end the turn so the queue runs. In a chat session print those two lines and ask the user to send them in that order. Step 9 of `on` sets a 300k auto-compact window as a safety net, but auto-compact runs no handoff, so this manual pass must come first. Never do this while a `question` is unanswered; reply first.

Done-when and gate. `settle.sh` exit 2 fails the Task; read its JSON `errors` (missed item, evidence that does not hold, specHash, protected path, acceptance) and retry with "Previous attempt missed Done-when: <items>" plus the same Ownership/acceptance. Exit 0 still needs your eye on `unverifiedItems` (plain-text evidence) and on whether evidence matches the diff. Do not accept `succeeded` on an incomplete checklist even if tests pass. For multi-step Tasks prefer the `verify-<title>` Task over checking alone (`references/multi-agent-anti-skip.md`).

Skill use. In step 5, also check that the worker followed the Method line: a `worker_done` body or transcript (`worker-read --dispatch <id> --source transcript`) with no skill invocation for an implementation or bug Task is a verification issue; note it in the report and name the skill in the retry spec.

Failed Task. Retry exactly once, one rung up: a worker with the same model and a higher effort when one is idle, otherwise the next tier's worker; if no higher rung is open, tell the user what to open and wait (`references/model-routing.md`). Then `task-create` a new Task whose spec adds "Previous attempt failed: <reason from worker_done body>" and the same deps, dispatch it the same way (`dispatch.sh ... --attempt 2`), so the state file records `attempt: 2`. A verification failure for an incomplete Done-when checklist counts as the first failure even when worker_done said `succeeded`; create the retry Task the same way. A second failure stops that Task; continue Tasks that do not depend on it and list it in the final report.

**Step 8, Final report** (one message when every dispatch is settled, or when the run is stuck): first line done/not done; a table with one row per Task: title, tier, agent and terminal with its model and effort, attempts, outcome, commit hash or `no commit`, tests reported, your own verification (ok / issue with file:line), the performance checklist result per Task (ok, regression with file:line, or n/a per item); the questions forwarded to the user and the answers given; retried Tasks, the rung each retry used and why; one line with Tasks passed on the first attempt, passed on the retry, and stopped; anything left open and the next step. Also list every abandoned dispatch with its `abandon_reason` and every `keep_context` reason. Nothing between dispatch and this report except forwarded questions.

## Anti-skip rules for the coordinator

Applies to every worker type (claude, grok, codex, agy). The critical path is shell scripts, brief rules and your own verify; no agent hook is required (Claude Stop hooks are an optional extra). Schema and agent notes: `multi-agent-anti-skip.md`. `SKILL` is the absolute path of the skill directory (workers need absolute paths).

- **Completion only through the gate.** Every brief tells the worker to finish ONLY with `bash SKILL/scripts/orca-done.sh --from $W --task $TASK --dispatch $DISPATCH --checklist <path> --report <path> --expect-spec-hash <specHash> --summary "..."` (`--outcome failed` when an item is not done). It runs `verify-checklist.py` (items and evidence, report, protected-path diff, acceptance reruns) and sends `worker_done` only on pass, with the DONE-WHEN lines built from the checklist file. Raw `orchestration send --type worker_done` is allowed only when the script is unavailable in that terminal and you answered the worker's `ask` saying so.
- **Short brief plus worker contract.** Reply/ask/heartbeat/escalation commands, the start ritual, mark-item/write-report usage, completion rules and the Method/Context lines live once in `<pane>/.orca/worker-contract.md` (`scripts/install-worker-contract.sh`, step 8b of `on` in `setup.md`). The per-dispatch brief is the short form in "Compact brief template". Never paste the contract text, the Method/Context lines or the ask rule into a brief.
- **Checklist before dispatch, trusted specHash.** `scripts/dispatch.sh` runs `init-checklist.py` (items are the spec's Done-when list), which writes `<pane>/.orca/tasks/<taskId>/checklist.json` and prints its `specHash` (sha256 over taskId, baseRef, items[id, step], protectedPaths, acceptanceCommands). `.orca/` is worker-writable, so the checklist's own `specHash` field proves nothing: the coordinator stores the printed value as `dispatches[].spec_hash` (plus `specs/<task>.json`) and always verifies with `--expect-spec-hash` (what `settle.sh` does). A hash mismatch or missing `spec_hash` fails before any acceptance command runs.
- **Parallel only on disjoint paths.** Put Tasks in one wave only when their Ownership and Protected paths do not overlap (no file or prefix in one Task's Ownership is in another's Ownership or Protected); otherwise serialize them with `--deps`. A concurrent Task that edits another Task's protected path makes that Task's gate fail falsely (`--protected-diff` reads the whole worktree); judge such a hit from `git log` before retrying.
- **One concern per Task.** Prefer one Task per concern chained with `--deps`. Steps that depend on each other go to one worker as a chain; fan out to several workers only when the work is large, the parts are independent, and each part has its own check (`model-routing.md`). For multi-step or risky work add a `verify-<title>` Task (section "Verify Task").
- **Spec fields** (in addition to step 1): Original goal (user's words), In scope, Out of scope, Done-when (numbered, same as the checklist), Protected paths (tests, fixtures, snapshots, CI config, plus the Ownership of already settled Tasks; never the Ownership of a Task in the same wave, which the wave rule keeps disjoint and step 5 checks from the diff; never weaken or skip tests). Start ritual, heartbeat, evidence, ask and completion rules are in the worker contract and `orca-done.sh`; never restate them in the spec or brief.
- **Frozen gate.** Workers run as your OS user in bypass mode and can edit this skill's scripts. `dispatch.sh` therefore freezes the gate scripts read-only into `<state dir>/gate/<hash>/` (first dispatch of a state file; later dispatches stop with exit 4 if the skill's scripts changed, `--accept-gate-change` only when you changed them) and prints that copy's `settle.sh` as `settle`. Always run that frozen `settle.sh`; it fails the Task when the copy no longer matches its hash or when the skill dir's gate scripts differ from it (the worker's `orca-done.sh` ran from the skill dir).

## State file

State file: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self-handle>.json` (`:` in a chat address becomes `_`), holding `{run_id, self_handle, pane_path, roster[], opened[], autocompact[], skill_family, started, objective, gate, dispatches[], frontier_accepted, context_resets}` (`opened[]` = handles of terminals this role created; each `roster[]` entry also carries `model` and `effort` when known, else `unknown`); each `dispatches[]` entry is `{task_id, dispatch_id, terminal, agent, title, base_title, wave, status, attempt, started, settled, spec_hash, checklist, report, spec_path, pane, gate_hash, verify}` and is updated at every dispatch, settlement and retry (`scripts/dispatch.sh` and `scripts/settle.sh` write it under a lock). `spec_hash` is the trusted specHash of the Task's checklist; `<state dir>/specs/<task>.json` is the coordinator-side copy of the hashed spec and `<state dir>/briefs/<task>.txt` the brief sent. Both live outside the worktree, so workers cannot rewrite them. It is how the role survives compaction or a new session.

New keys written by code: `dispatches[].tier`, `model`, `effort` (the Task's tier and the worker's roster model and effort), `tier_override` (reason, only when a mismatch was let through), `frontier_quote`, and the top-level `frontier_accepted` map (`<task id>` to `{quote, at}`, written by `orch_state.py accept-frontier` or `dispatch.sh --frontier-accepted`). The final report reads them from the entry (`orch_state.py get <state> <task> tier`).

Context and open-dispatch keys: the top-level `context_resets` map (`<terminal handle>` to `{at, handoff}`, written by `orch_state.py context-reset`, which `fresh-worker.sh` runs after its `/clear`; `handoff` is the path of the handoff file); `dispatches[].keep_context` (the reason passed with `--keep-context`); and the status `abandoned`, set by `orch_state.py abandon <state> --reason "<why>" --user-said "<the user's words>" (--task <id> | --terminal <handle> | --all)` together with `abandoned_at`, `abandon_reason` and `abandon_user_said`. `abandon` gives a Task up: it only changes entries that are `dispatched` or `active`, calls no Orca command (release or fail the Orca-side dispatch yourself when needed), and exits 2 without `--user-said`. It is never the way to close a finished Task, because it skips the gate; a finished Task is closed by the frozen `settle.sh`. When `settle.sh` fails for a reason that is not the worker's (for example a gate script bug), stop and tell the user.

Work log: `python3 $SKILL/scripts/worklog.py recent | search <words> | show <task id>` reads the briefs under `<state dir>/briefs/`, the state entries and the workers' reports of this pane (earlier Runs included) and prints, per Task, the time, status, title, Change, the user's words and the report summary. It writes nothing. Search it before you ask the user whether something exists or was done, before you write a spec for an area, and before you forward a worker's `question`; then read the report. The work log knows Tasks, not code. For the code (where a feature lives, who calls it, what a change breaks) use the GitNexus tools (`query`, `context`, `impact`) or the Serena tools (symbol search and references) when the session has them; fall back to `git log -- <path>` and grep only when it has neither or the repository is not indexed. Ask the user only when those are silent. The resume hook prints the exact command and the last 6 Tasks. An abandoned entry no longer blocks its terminal, no longer counts as open for the resume hook, and still counts as a Task of that terminal's current context. `orch_state.py roster-set <state> <handle> --model M [--effort E]` updates a roster entry after a model switch (`fresh-worker.sh --model` runs it).
