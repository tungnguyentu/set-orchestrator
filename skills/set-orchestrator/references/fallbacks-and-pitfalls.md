# Fallbacks and pitfalls

Read this when a worker agent does not send `worker_done`, when an agent type is not known to honor the Orca preamble, or when an Orca command behaves unexpectedly.

## Fallback (two separate branches)

**Branch A, agent type known not to honor the Orca preamble.** Skip Task and Dispatch (so there is nothing to abandon) and use the feedback-block path: generate a nonce (`openssl rand -hex 3`), send the same spec with `ORCA terminal send --terminal <h> --text "$(cat <brief>)" --enter --wait-submit 15 --json`, and end the brief with: "MANDATORY when done: print a block that opens with the line `FB-START` and closes with the line formed by joining `FB-` + `<nonce>` + `-END` (no spaces). Inside: files and lines changed; summary; result of each verification command; commit hash; anything not done and why." The agent assembles the closing line, so the brief echoed on screen cannot match it. Poll in the background, about 20 minutes at most: every 20 s `ORCA terminal read --terminal <h> --limit 400 --json`, join `result.terminal.tail`, and stop when it contains `FB-<nonce>-END`; then verify as in loop step 5. On timeout read the screen and tell the user where the agent is; do not send more input unless the user asks. Which non-Claude/Codex agents ignore the preamble is **not yet verified**: the spike only tested Claude, and a Grok/Gemini behavior is unproven. Treat them as Branch A candidates only after the user agrees or the first attempt shows no `worker_done`. Branch A agents still complete through the gate: their brief's completion line is `bash SKILL/scripts/orca-done.sh --task <label> --checklist <path> --fb-nonce <nonce> --summary "..."`, which verifies the checklist and then prints the `FB-START ... FB-<nonce>-END` block itself (details in `references/multi-agent-anti-skip.md`). <!-- anti-skip:multi-agent:brancha -->

**Branch B, preamble-capable agent that finishes without sending `worker_done`.** Absence never authorizes abandon. `worker-abandon --dispatch <id>` only with positive proof: `ORCA orchestration worker-read --dispatch <id> --source transcript` shows the agent's final turn sent no `worker_done`, or an `FB-<nonce>-END` block has appeared. An empty wait, a timeout, an unchanged tail, or `unverifiable` liveness is only a checkpoint: keep `check --wait` or follow `worker-list` `projection.nextAction`. After abandon, retry with `worker-start --retry-of <dispatch_id>` or switch that terminal to Branch A.

## Pitfalls

- `worktreePath` differing from the pane (even the same agent type): not eligible. The parent repo counts; a submodule path does not.
- `ORCA status --json` has no caller handle outside a terminal; do not infer self from it.
- `--worktree current` outside a terminal silently resolves to the coordinator's worktree; pass `path:"<pane>"`.
- `worker-start` can exit 1 with `ok:true` and `state:failed`; it still creates a Task + Dispatch: `worker-release` it and `task-update --status failed` if dropped.
- Busy start (`already has an active dispatch`): wait or pick another terminal, never retry the same command.
- Agent stuck on a dialog (for example a usage-credits prompt) still returns `input_accepted`; only send Enter to terminals you own.
- `tui-idle` is not completion. Use `worker_done` (or the nonce in Branch A).
- Never `terminal close` an owned worker (use `worker-release`); never close a user's terminal.
- Ack a Delivery only after every message in it is handled; a bound Run replays it otherwise.
- `worker_done` settles the Task automatically; do not also `task-update --status completed`.
- Several agents share one worktree: tell each its branch and forbid `git add -A`; a Codex sandbox may not write `.git`, so verify the tree and commit on its behalf.
- Heartbeats come every ~5 minutes from workers; a quiet worker inside `ask` or `check --wait` is alive.
