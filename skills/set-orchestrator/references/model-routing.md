# Model routing: cost per finished Task

The number to keep low is what it costs to get a Task through the gate, not the price of a token. A cheap worker that needs two retries and a manual fix costs more than a stronger worker that passes once. A strong worker on a mechanical Task wastes money the other way. Route by the Task, verify with the gate, and raise the rung only after a failure.

Prices and model names change. Never quote a price from memory; when the user asks what a run costs, read the current price list.

## Tiers

Pick the lowest tier whose description fits the Task. `Tier` goes in the Task spec.

| Tier | Task shape | Claude worker | Other agents |
|---|---|---|---|
| `light` | Extraction, classification, log or file reading, search across many files, mechanical renames with a test that proves them | `haiku` | their small or fast model |
| `standard` | Clearly specified change with exact acceptance commands; routine bug fix with a known cause; tests for existing code | `sonnet` | their default model |
| `heavy` | Design decision inside the Task, debugging with an unknown cause, cross-module change, anything the user called risky | `opus` | their strongest model |
| `frontier` | Large migration or a research-grade problem that `heavy` already failed, or the user asked for it. Only with the user's yes for that Task (next section) | `fable` | none; ask the user |

### Tier guard in `dispatch.sh`

The tier is checked by code, not by memory. The state file's `roster[]` is the only source of a worker's model; `scripts/orch_state.py` maps it to a tier in one table (`orch_state.py model-tier <model>`): a name containing `haiku` is `light`, `sonnet` is `standard`, `opus` is `heavy`, `fable` is `frontier`; a missing, empty, `unknown` or unrecognized model (codex, grok and other agents) is `standard`. You choose the Task's tier; the script never classifies a Task.

- `dispatch.sh --tier <tier>`: when the target worker's tier differs, it exits 5 before anything is created and prints the Task tier, the worker's title, model and tier, and the roster workers whose tier matches (idle or not; check idleness yourself). Send the Task to one of them, switch a worker (below), or pass `--tier-override "<reason>"`, which lets it through and stores the reason as `tier_override` in the Dispatch entry. Use the override only for the cases this file allows (for example the nearest idle tier for an agent that cannot be switched) and name it in the report.
- Without `--tier` the dispatch still runs and prints a warning; always pass it.
- The entry records `tier`, `model` and `effort`; they are read back for the final report (`orch_state.py get <state> <task> tier`).

The orchestrator session itself plans, writes specs and verifies. That is `heavy` work; do not run the orchestrator on a `light` model.

## Frontier needs the user's yes

`fable` runs only after the user accepts it for that Task. This holds for every way a Task can reach it:

- sending a Task to a worker that already runs `fable`, even when that worker is idle and nothing else is;
- switching a worker to `fable` with `/model`;
- a retry whose next rung is `frontier`;
- a subagent model named in a spec or a brief.

Ask once per Task, in one line: the Task, why `heavy` is not enough (or that `heavy` failed the gate, with the failing item), and the alternative if the user says no. Then wait. Other Tasks keep running while you wait.

A yes covers that Task only, not later Tasks and not the rest of the Run, unless the user says so in plain words ("use fable for all of these"). Record the acceptance with the user's own words: `python3 scripts/orch_state.py accept-frontier <state file> <task id> --quote "<the user's words>"` (an empty quote is refused), or, when the Task does not exist yet, pass `dispatch.sh --frontier-accepted "<the user's words>"`, which writes the same record once the Task id is known. The record is `frontier_accepted[<task id>] = {quote, at}` in the state file, the entry keeps it as `frontier_quote`, and the final report lists it.

`dispatch.sh` enforces this. A Task counts as frontier when `--tier frontier` is given or the target worker's model maps to `frontier`; without an acceptance record for that Task (an `accept-frontier` record plus `--task <id>`, or `--frontier-accepted`) it exits 6 before the brief is sent and before the Task is created. `--tier-override` never bypasses it. A `fable` worker given a non-frontier Task is a tier mismatch and a frontier dispatch at once: the acceptance alone is enough, no override is needed. A `frontier` Task sent to a worker that is not on `fable` is still a tier mismatch and needs `--tier-override` as well as the acceptance. On a no or no answer, stay on `heavy`: run the Task there, or stop a failed Task after its `heavy` attempt and report it.

The user naming `fable` in their own request (`--open claude@fable`, "give this to fable") is the yes for the Tasks that request names. A worker the user opened on `fable` for one Task is not a yes for the next one: treat it as a `heavy` worker for routing and ask before sending it `frontier` work, or leave it idle when a cheaper worker fits.

## Opening workers with a model

`--open` takes `<agent>[:<count>][@<model>[/<effort>]]`:

```text
--open claude:2@sonnet,claude@opus/high,codex,grok@grok-code/low
```

`open-agents.sh` passes the model and effort to the agent's own flags and prints them in each JSON row. Store them in the state file's `roster[]` entry as `model` and `effort`. A terminal the user opened by hand has no row: read its status line with `terminal read`, or record `unknown`; `dispatch.sh` treats an unknown model as `standard`.

Without `@model` the agent starts on its own default. That is fine for a first run; say in the report which model each worker used.

## Verify first, escalate after

1. Write the Task with observable Done-when items and exact acceptance commands. A Task that cannot be checked cannot be routed cheaply.
2. Send it to an idle worker of the Task's tier. If none is idle, switch an idle Claude worker to that tier's model (next two sections). If no Claude worker can be switched, use the nearest tier that is idle and say so in the report. Opening a new terminal still needs the user's request.
3. Let the gate decide (`settle.sh`). A pass on the first attempt is the cheap path and the normal one.
4. On a gate failure, the one retry goes up exactly one rung, in this order:
   - same model, higher effort, when a worker with that model and a higher effort is idle;
   - otherwise the next tier's worker. When the next tier is `frontier`, ask the user first (section "Frontier needs the user's yes").
   Never retry on the same model and effort with the same spec: that buys the same failure twice. If no higher rung is open, tell the user what to open (for example `--open claude@opus/high`) and wait.
5. A second failure stops the Task as before. Report it with both rungs tried.

Do not start a Task above its tier "to be safe". The retry is what protects the result; the tier is what protects the bill.

## Changing a model on a running worker

Avoid it in the middle of a Task. A long session that changes model has to rebuild its prompt cache, and reasoning from the stronger model does not carry over cleanly to a weaker one.

Change only at a Task boundary, on an idle Claude terminal whose Dispatch is settled, and do it yourself when a Task's tier has no idle worker. Prefer the worker with the least context to lose, and one whose current model is nearest the target. Run `bash scripts/fresh-worker.sh --terminal <h> --model <name> [--effort <level>]` ("Handoff, then clear" below): it does the handoff, the `/clear`, the `/model` and `/effort` sends and the roster update in one go. Other agents cannot be switched in place: open a new terminal with the model instead, which needs the user's request.

## Handoff, then clear

A `/clear` throws away what the worker knows. Every `/clear` you send to a worker, for a model switch or for a full context, comes after a handoff that is on disk.

Run it with `bash scripts/fresh-worker.sh --terminal <h> [--model <name> [--effort <level>]] [--state <file>] [--pane <path>] [--timeout-ms N] [--dry-run]`. It does the steps below and nothing else: it exits 3 (nothing sent) while the terminal has an open Dispatch, 4 when it is not idle, 5 when no fresh handoff file appears after two requests (it never sends `/clear` then); after the `/clear` it records `context_resets[<handle>] = {at, handoff}` in the state file and prints one JSON line `{ok, terminal, handoff, model, effort}`. `dispatch.sh` counts Tasks per terminal since that record: a Claude worker that already took 3 (limit set by `--max-per-context N` or `ORCH_MAX_TASKS_PER_CONTEXT`) is refused with exit 8 until this script has run, and `--keep-context "<reason>"` lets a Task that continues the previous one through. Codex, Grok and Antigravity workers are not counted and have no `/clear` here. The manual steps:

1. The terminal is idle and its previous Dispatch is settled. Never do this to a worker with an active Dispatch.
2. Ask for the handoff: `ORCA terminal send --terminal <h> --enter --text "Write your session handoff now to plans/handoffs/session-handoff-<handle>.md, as the Context section of .orca/worker-contract.md says, then stop."` The contract names the handoff skill for the pane's skill family, or says to write it by hand.
3. `ORCA terminal wait --terminal <h> --for tui-idle --timeout-ms 300000`, then check that `<pane>/plans/handoffs/session-handoff-<handle>.md` exists and was modified after step 2 (`ls -l`).
4. No fresh file: do not clear. Ask once more; if it still fails, leave that worker as it is, use another worker or the nearest tier, and tell the user.
5. Send `/clear` and wait for `tui-idle`.
6. Model switch only: send `/model <name>`, then `/effort <level>` when the tier needs it, wait for `tui-idle`, and write the new `model` and `effort` into the worker's `roster[]` entry.
7. Send the brief. When the new Task continues that worker's earlier work, name the handoff file in the brief's `Change:` line so the worker reads it first.

A model switch is recorded in the final report: worker, from, to, and the Task that needed it.

Why the limit: a worker's answers get worse as its context fills, and a measured Run showed workers that took dozens of Tasks on one context (many auto-compacts, briefs arriving at 150k to 265k tokens). A fresh context costs one handoff per three Tasks, which is cheaper than a bloated one.

## One worker or several

Parallel workers pay off only when the work is large, splits into parts that do not depend on each other, and each part has its own check.

- Steps that depend on each other go to one worker as one Task chain. Splitting them adds a context per worker and loses detail in every handoff summary.
- Wide, shallow work (read many logs, search many files, apply one checked change in many places) fans out to several `light` or `standard` workers, with one `heavy` Task to combine and verify.
- A Task under about ten minutes of work is not worth a second worker.

## Subagents inside a worker

A worker that starts its own subagents sets their model explicitly. Defaults differ between versions and can be the session's own expensive model. Search and log reading go to a small model; the worker keeps the decision.

## Context is not free

A large context window is a limit, not a target. Keep briefs short, keep the `/clear` rule for reused workers (`fresh-worker.sh`; `dispatch.sh` enforces it), and keep the 300k auto-compact window. Cheap cached input does not repair the quality loss of a bloated context.

## What to measure

Per Task, in the state file and the final report: tier, worker model and effort, any `tier_override` reason, any frontier quote, attempts, gate result. Per run: Tasks passed on the first attempt, Tasks that needed the retry rung, Tasks stopped. When first-attempt passes on a tier drop, raise that tier's default for this repository and tell the user; when they stay near total, try one tier lower on the next similar Task.
