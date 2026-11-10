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
| `frontier` | Large migration or a research-grade problem that `heavy` already failed, or the user asked for it | `fable` | none; ask the user |

The orchestrator session itself plans, writes specs and verifies. That is `heavy` work; do not run the orchestrator on a `light` model.

## Opening workers with a model

`--open` takes `<agent>[:<count>][@<model>[/<effort>]]`:

```text
--open claude:2@sonnet,claude@opus/high,codex,grok@grok-code/low
```

`open-agents.sh` passes the model and effort to the agent's own flags and prints them in each JSON row. Store them in the state file's `roster[]` entry as `model` and `effort`. A terminal the user opened by hand has no row: read its status line with `terminal read`, or record `unknown` and treat it as `standard`.

Without `@model` the agent starts on its own default. That is fine for a first run; say in the report which model each worker used.

## Verify first, escalate after

1. Write the Task with observable Done-when items and exact acceptance commands. A Task that cannot be checked cannot be routed cheaply.
2. Send it to an idle worker of the Task's tier. If none is open, use the nearest tier that is open and say so in the report. Opening a new terminal still needs the user's request.
3. Let the gate decide (`settle.sh`). A pass on the first attempt is the cheap path and the normal one.
4. On a gate failure, the one retry goes up exactly one rung, in this order:
   - same model, higher effort, when a worker with that model and a higher effort is idle;
   - otherwise the next tier's worker.
   Never retry on the same model and effort with the same spec: that buys the same failure twice. If no higher rung is open, tell the user what to open (for example `--open claude@opus/high`) and wait.
5. A second failure stops the Task as before. Report it with both rungs tried.

Do not start a Task above its tier "to be safe". The retry is what protects the result; the tier is what protects the bill.

## Changing a model on a running worker

Avoid it in the middle of a Task. A long session that changes model has to rebuild its prompt cache, and reasoning from the stronger model does not carry over cleanly to a weaker one.

Change only at a Task boundary, on an idle Claude terminal whose Dispatch is settled: send `/clear`, wait for `tui-idle`, then `/model <name>` (and `/effort <level>`), wait again, and update `roster[]`. Other agents: open a new terminal with the model instead.

## One worker or several

Parallel workers pay off only when the work is large, splits into parts that do not depend on each other, and each part has its own check.

- Steps that depend on each other go to one worker as one Task chain. Splitting them adds a context per worker and loses detail in every handoff summary.
- Wide, shallow work (read many logs, search many files, apply one checked change in many places) fans out to several `light` or `standard` workers, with one `heavy` Task to combine and verify.
- A Task under about ten minutes of work is not worth a second worker.

## Subagents inside a worker

A worker that starts its own subagents sets their model explicitly. Defaults differ between versions and can be the session's own expensive model. Search and log reading go to a small model; the worker keeps the decision.

## Context is not free

A large context window is a limit, not a target. Keep briefs short, keep the `/clear` rule for reused workers, and keep the 300k auto-compact window. Cheap cached input does not repair the quality loss of a bloated context.

## What to measure

Per Task, in the state file and the final report: tier, worker model and effort, attempts, gate result. Per run: Tasks passed on the first attempt, Tasks that needed the retry rung, Tasks stopped. When first-attempt passes on a tier drop, raise that tier's default for this repository and tell the user; when they stay near total, try one tier lower on the next similar Task.
