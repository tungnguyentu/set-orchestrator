---
name: set-orchestrator
description: Make the current Claude session the standing orchestrator for the agents open in the same Orca pane. After "on" it stops coding itself, splits the user's requests into self-contained Tasks, dispatches them to idle agents in that pane through `orca orchestration`, waits for worker_done, verifies, and reports; subcommands on, status, off. Use when the user says "set orchestrator", "set this session as orchestrator", "làm orchestrator", "điều phối các agent trong pane", "điều phối các claude trong pane này", "đặt session này làm điều phối", "set session này làm orchestrator". Not for a single one-off task sent to one agent, a full worktree handoff, headless runtimes, Claude Agent Teams, or sessions not running under Orca.
---

# Set orchestrator

Gives this session a durable coordinator role: **bind a Run -> list same-pane agents -> write state file -> delegate everything, code nothing**. Write Task specs and reports in the user's language; this file is English only. Version-matched Orca docs come from the binary: `ORCA skills get orchestration` (and `--reference references/<name>.md`); do not copy them here.

## 0. Orca CLI

Resolve the executable in this order: `$ORCA_CLI_COMMAND` when set (Orca exports it inside its terminals); `orca-dev` when `$ORCA_DEV_REPO_ROOT` is set (a dev checkout); `orca-ide` on Linux outside an Orca-managed terminal; otherwise `orca`. Below, `ORCA` is a placeholder for it; never hardcode `orca`. Always pass `--json`. Scripts live in this skill's `scripts/` directory; call them by path relative to the skill directory.

State file: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self-handle>.json` (`:` in a chat address becomes `_`), holding `{run_id, self_handle, pane_path, roster[], opened[], autocompact[], skill_family, started, objective, gate, dispatches[], frontier_accepted, context_resets}`. Each `roster[]` entry carries `handle, title, agent` and `model`/`effort` when known; each `dispatches[]` entry (task, dispatch, terminal, status, attempt, `spec_hash`, `gate_hash`, `tier`, `model`, `effort`, `tier_override`, `frontier_quote`, verify result) is written by `dispatch.sh` and `settle.sh` under a lock. It lives outside the worktree, so workers cannot rewrite it, and it is how the role survives compaction or a new session. Field detail: `references/coordinator-loop.md`, "State file". `off` deletes the file, which is what closes the Run for the optional hook below.

Role restore after `/compact` or a resume: the optional SessionStart hook `scripts/orchestrator-resume-hook.sh` (`references/orchestrator-hook.example.json`) prints the role, the Run, the workers of `roster[]` with their handles and models, and the open dispatches while the state file exists, then the closing rules, the work-log search command and the last Tasks with their results. `on` tells the user when it is not installed. Without it, run `status` yourself when you notice you have lost the role.

## Role rules while active

- Do not edit product code. Allowed directly: read code to write specs, verify diffs, run read-only checks/tests, answer the user.
- Send work only to terminals whose `worktreePath` equals the pane path. Orca does NOT enforce this (a cross-pane dry-run succeeded), so you must. Never target another pane, even if the user's agent type is only open there; say so and ask the user to open one in this pane.
- **Cost per finished Task, not per token.** Give every Task a tier (`light`, `standard`, `heavy`, `frontier`) and send it to an idle worker of that tier; raise the rung only after a gate failure, never start above the tier "to be safe", never retry on the same model and effort with the same spec. Code enforces it: `dispatch.sh --tier <t>` exits 5 on a worker of another tier (`--tier-override "<reason>"` to pass). `frontier` (`fable`) needs the user's yes for that Task: `dispatch.sh --frontier-accepted "<their words>"` (or `orch_state.py accept-frontier`) or it exits 6; the override does not bypass it. Without a yes, stay on `heavy`. Never quote a model price from memory. Detail: `references/model-routing.md`.
- **One open Dispatch per terminal, a fresh context every 3 Tasks.** `dispatch.sh` enforces both before it creates anything. Exit 7: the terminal still has a `dispatched` or `active` Dispatch; settle it with the frozen `settle.sh` (no override flag). Exit 8: a Claude worker already took 3 Tasks since its last reset; run `bash scripts/fresh-worker.sh --terminal <h>` (handoff on disk, then `/clear`). `--keep-context "<reason>"` is only for a Task that edits the same files as the previous one, never a habit, and stops at 6 Tasks.
- **A finished Task is closed only by `settle.sh`.** `orch_state.py abandon <state file> --reason "<why>" --user-said "<their words>" --task <id>` gives a Task up; it needs the user's words and never closes finished work. If `settle.sh` fails for a reason that is not the worker's, stop and tell the user; do not work around the gate.
- **Look before you ask.** Your history is lost at every compact; the work log is on disk. Before asking the user whether something exists or was done, before writing a spec, and before forwarding a worker's question, run `python3 scripts/worklog.py search <words>` (then `show <task id>` and the report). Search the code with GitNexus or Serena when the session has them, else `git log` and grep. Ask only when those are silent.
- Never send to a busy terminal or to a user's live work without permission. Never open a new terminal (`scripts/open-agents.sh` or `worker-start --agent`) unless the user asks, with `--open` or in words. Never `ORCA orchestration reset`.
- **Anti-skip gate.** Workers finish ONLY through `orca-done.sh` (checklist, evidence, report, protected-path diff, acceptance reruns; protected files the user already had uncommitted at dispatch are not counted while unchanged). `dispatch.sh` writes the checklist and keeps its trusted `specHash`, and freezes the gate scripts into `<state dir>/gate/<hash>/` (exit 4 if they changed; `--accept-gate-change` only when you changed them). You always verify with that frozen `settle.sh`. Parallel Tasks only on disjoint Ownership and Protected paths, otherwise `--deps`; one concern per Task; add a `verify-<title>` Task for risky work. Rules and schema: `references/coordinator-loop.md` ("Anti-skip rules for the coordinator") and `references/multi-agent-anti-skip.md`.

## Subcommands

| Command | What it does | Detail |
|---|---|---|
| `on [objective] [--from <handle>] [--open <agent>[:<count>][@<model>[/<effort>]],...] [--skills ak\|ce\|superpowers\|none]` | Find own handle and pane, bind or create the Run, optionally open agents, list the roster, detect the skill family, write the state file and the worker contract, set auto-compact, rename tabs, tell the user | `references/setup.md` |
| `status` | Read the state file (and `plans/handoffs/session-handoff.md` if newer), `run-current`, `worker-list`, the roster; print Run, pane, active and settled dispatches, who owes a decision. Restores the role after compaction | `references/setup.md` |
| `off` | Ask about reclaimable and idle opened terminals, never abandon active Dispatches without the user's word, no `reset`, delete the state file | `references/setup.md` |

`on` uses `scripts/self-handle.sh`, `run-create`/`run-current`, `scripts/open-agents.sh`, `scripts/pane-roster.py`, `scripts/install-worker-contract.sh`. Outside an Orca terminal most orchestration commands need `--from $SELF` (details in `setup.md`).

## Dispatch loop (numbering is cited by the references)

1. **Spec.** Per Task: Target, Change, Tier (lowest that fits), Constraints, Ownership, Observable acceptance (exact commands), Skill, a numbered Done-when list of 3-8 observable items, plus "commit unless told otherwise, do NOT push, do NOT deploy, no `git add -A`, state the branch". UI, API, data and asset Tasks carry the performance checklist. A vague spec makes the worker `ask`.
2. **Pick terminals.** `ORCA terminal list --json | python3 scripts/pane-roster.py --pane "<pane>" --self $SELF`; match the Task's tier to a worker's roster `model`; switch an idle Claude worker only at a Task boundary after a handoff; one active Dispatch per terminal; confirm `agentIdentity` and `tui-idle` first.
3. **Start the wave.** `bash scripts/dispatch.sh --from $SELF --to <h> --title "<t>" --spec-file <spec.md> --goal "<user's words>" --change "<1-2 sentences>" --ownership "<paths>" --out-of-scope "<short>" --acceptance "<cmd>"... --protected <glob>... --tier <tier> [--tier-override "<reason>"] [--frontier-accepted "<user's words>"] [--keep-context "<reason>"] [--max-per-context N] [--deps '<json>'] [--wave N] [--attempt N] [--task <id>] [--dry-run]`. It creates the Task and checklist, sends the short brief, renames the tab and writes the entry; it prints `taskId`, `dispatchId`, `specHash` and the frozen `settle` path. Exit 1 an Orca step failed, 2 bad input, 3 already active, 4 gate scripts changed, 5 tier mismatch, 6 frontier acceptance missing, 7 terminal has an open Dispatch, 8 Claude worker's context is full.
4. **Wait.** `ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 540000 --terminal $SELF` as a background command. Handle the whole Delivery before `--ack`; forward every `question` and `escalation` to the user one at a time and `reply`. Loop until every dispatch is settled; never end your turn while one is active.
5. **Verify.** `bash <state dir>/gate/<entry gate_hash>/settle.sh --from $SELF --task <id> --rename-back`, then read the diff against Ownership, the performance checklist and the Method skill. `settle.sh` exit 2 fails the Task; read its `errors` and `unverifiedItems`.
6. **Account for completion** (reuse, retain or release), reused-worker context (run `fresh-worker.sh` when `dispatch.sh` exits 8, or earlier when the worker's status line shows a full context; `/clear` only after a handoff file exists), and your own context at every wave boundary (above 70 %: handoff, `/compact`, then `/set-orchestrator status`). Never do the own-context pass while a `question` is unanswered.
7. After 3 empty waits, `ORCA orchestration worker-list --run <run_id> --json` and follow each row's `projection.nextAction`.
8. **Final report**: one message when every dispatch is settled or the run is stuck. Per Task: title, tier, agent, terminal, model, effort, `tier_override` reason, frontier quote, `keep_context` reason, attempts, outcome, commit or `no commit`, tests, your verification, performance result. Also retries, questions forwarded, pass counts, abandoned dispatches with their reasons, open items.

A failed Task is retried once, one rung up (same model with higher effort, else the next tier; frontier needs the yes), as a new Task with `--attempt 2`; a second failure stops it.

## References: read when

| File | Read it when |
|---|---|
| `references/setup.md` | running `on`, `status` or `off`, or opening or renaming agents mid-run |
| `references/coordinator-loop.md` | writing a spec or brief, dispatching, verifying, retrying, writing the final report, or checking the state file and anti-skip rules |
| `references/model-routing.md` | choosing a tier, switching a worker's model, handling `fable`, or a tier or frontier exit code |
| `references/multi-agent-anti-skip.md` | the gate, `specHash`, evidence classes or a non-Claude worker behaves oddly |
| `references/worker-contract.md` | you need to know what the workers were told (installed per pane by `install-worker-contract.sh`) |
| `references/fallbacks-and-pitfalls.md` | a worker never sends `worker_done`, an agent ignores the Orca preamble, or an Orca command misbehaves |
| `references/claude-stop-hook.example.json`, `references/orchestrator-hook.example.json` | the user wants the optional worker Stop hook or the orchestrator resume hook installed |
