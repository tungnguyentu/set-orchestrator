# Multi-agent anti-skip contract

Workers in one pane can be any mix of `claude`, `grok`, `codex` and `agy` (Antigravity). The critical path below uses only shell scripts, plain JSON files, the brief text and the coordinator's own verification, so it works the same for every agent. Agent hooks (Claude Stop hooks) are optional extras on top; nothing depends on them. `SKILL` below is the absolute path of this skill directory; `ORCA` is the resolved executable (SKILL.md section 0).

## 1. Shared contract (every agent)

1. The coordinator writes the Done-when list into `<pane>/.orca/tasks/<taskId>/checklist.json` before dispatch (`dispatch.sh` -> `init-checklist.py`) and keeps the printed `specHash` in its state file (`dispatches[].spec_hash`, section 2a).
2. The worker works the items in order, one at a time, and after each item records real evidence: `python3 SKILL/scripts/mark-item.py --checklist <path> --id N --evidence "<command -> exit 0 | commit abc123 | path/file.ext:LINE>"` (or `--run "<cmd>"`, which records `<cmd> -> exit N` and passes only on exit 0; `--expect-fail` for a write-a-failing-test step). It never marks an item it did not do.
3. Long output goes to the report file (`write-report.py`), not into the worker_done body.
4. The worker completes ONLY with `bash SKILL/scripts/orca-done.sh ...`. The script runs `verify-checklist.py` (items, report, protected paths, acceptance commands) and sends `worker_done` only when the gate passes. A failed gate prints the misses and sends nothing.
5. When the worker cannot finish, it still uses the script, with `--outcome failed --summary "<what is not done and why>"`: the body then lists each item as done or skipped, honestly.
6. Raw `orca orchestration send --type worker_done` is allowed only when the script itself is unavailable in that terminal and the coordinator answered an `ask` saying so.
7. The coordinator verifies anyway (section 10). The gate makes skipping visible; it does not replace review.
8. Short brief + contract file. <!-- worker-contract:anti-skip --> Items 2-6, the start ritual (section 8), heartbeat format (section 7) and the Method/Context lines are written once per pane in `<pane>/.orca/worker-contract.md` (`scripts/install-worker-contract.sh`, template `references/worker-contract.md`). Each dispatch sends only the short brief (`references/coordinator-loop.md`): ids, checklist/report/skill paths, the `Finish ONLY: … orca-done.sh …` line and a one-line Goal/Change/Ownership/Out of scope/Acceptance. The checklist file carries the numbered items, protected paths and acceptance commands, so brief length does not weaken the gate.

## 2. checklist.json schema

```json
{
  "taskId": "task_123",
  "version": 1,
  "items": [{"id": "1", "step": "Add failing test in src/a.test.ts for X", "passes": false, "evidence": ""}],
  "protectedPaths": ["tests/", "**/*.snap", ".github/"],
  "acceptanceCommands": ["npm test -- a.test.ts", "npm run build"],
  "originalGoal": "Users can reset 2FA from settings",
  "worker": "term_abc",
  "baseRef": "<HEAD sha when the checklist was created>",
  "specHash": "<sha256 of the canonical spec, see section 2a>"
}
```

- `items`: 3-8 observable steps (same rules as the Done-when list). Workers change only `passes` and `evidence`, preferably through `mark-item.py`.
- `specHash`: convenience copy of the hash in section 2a. Worker-writable, so never trusted alone: without `--expect-spec-hash` a missing or mismatched field fails, but a worker can recompute it. Old checklists with only `stepsHash` fail; re-init with `--force`.
- `protectedPaths`: globs or path prefixes the worker must not change (tests it must make pass, fixtures, snapshots, CI config, lockfiles, the Ownership of already settled Tasks). Never list the Ownership of a Task running in the same wave: the wave rule keeps concurrent Tasks disjoint, and `--protected-diff` sees the whole worktree, so it would fail on the other worker's edits.
- `acceptanceCommands`: rerun by the gate in the repo root; each must exit 0.
- `worker`: terminal handle that owns the checklist; used only by the optional Claude Stop hook.
- `baseRef`: diff base for the protected-path check (covers committed changes too).
- `init-checklist.py` adds `.orca/` to `.git/info/exclude` (local only, not committed) so checklists and reports never land in a commit; pass `--no-exclude` to skip.
- `orca-done.sh` writes `done.json` next to the checklist after a successful send.

## 2a. specHash (trusted spec) <!-- anti-skip:spec-hash -->

- `specHash` = sha256 of `json.dumps({taskId, baseRef, items:[{id, step}], protectedPaths, acceptanceCommands}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` (`scripts/checklist_spec.py`; `python3 checklist_spec.py <checklist>` prints it). `passes`, `evidence`, `worker`, `originalGoal` are outside it, so `mark-item.py` never changes it.
- `init-checklist.py` prints it (`"specHash"` in its JSON line, `specHash=...` on stderr) and with `--spec-out <state dir>/specs/<task>.json` writes a coordinator-side copy of the hashed fields.
- The coordinator stores it in `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self>.json` -> `dispatches[].spec_hash` (`dispatch.sh` does both) and ALWAYS verifies with `verify-checklist.py --expect-spec-hash <spec_hash>` (or `--spec <specs/task.json>`); `settle.sh` reads it from the state file and fails when it is missing.
- `verify-checklist.py` recomputes the hash from the file contents first. Mismatch with the expected value (or, without one, a missing/mismatched stored field) fails the gate and skips the protected-diff and the acceptance commands, which run with `shell=True` and must never come from a tampered file. Clearing `acceptanceCommands`, clearing `protectedPaths`, moving `baseRef`, rewriting a step or deleting the hash field all fail.
- The brief's `Finish ONLY:` line carries `--expect-spec-hash`, so the worker's own gate catches an edited spec too; the coordinator's `settle.sh` is the gate that counts.

## 2b. Evidence classes

`verify-checklist.py` classifies the evidence of every item marked `passes: true`:

| Evidence | Check | Result |
|---|---|---|
| `commit <sha>` | `git cat-file -e <sha>^{commit}` in `--cwd` | unknown commit = fail |
| bare 7-40 hex token | same check | a commit = verified; anything else (docker id, uuid part) = ignored |
| `path/file.ext:LINE` or `:START-END` | file exists (relative to `--cwd`), lines within the file; a path without `/` counts only with a code/doc extension, so `127.0.0.1:8080` is not a file | missing file / out of range = fail |
| `<cmd> -> exit N`, N ≠ 0 | none | fail, unless it ends with `(expected fail)` (`mark-item.py --run ... --expect-fail`) |
| `<cmd> -> exit 0` | verified only when `<cmd>` is one of `acceptanceCommands` and this run uses `--rerun-acceptance` | otherwise a typed claim: ok, but listed in `unverifiedItems` |
| plain text | none | ok, but listed in `unverifiedItems` + a warning in the JSON summary for the coordinator |

URLs and `host:port` are not treated as files.

## 3. Scripts

| Script | Who runs it | Purpose |
|---|---|---|
| `scripts/init-checklist.py` | coordinator | Create `checklist.json` (`--task`, `--item`... or stdin, `--goal`, `--protected`..., `--acceptance`..., `--worker`, `--out`, `--force`) |
| `scripts/mark-item.py` | worker | Set one item `passes` + `evidence` (`--fail` keeps it false; `--run "<cmd>"` records `<cmd> -> exit N`) |
| `scripts/write-report.py` | worker | Merge a step into `report.json`; `--run "<cmd>"` executes the command and records exit code and output tail |
| `scripts/verify-checklist.py` | orca-done, coordinator, verify Task, optional hook | Gate; `--expect-spec-hash`/`--spec` integrity first, evidence classes, report, protected diff, acceptance; exit 0 pass, exit 2 with misses on stderr; JSON summary on stdout (`specIntegrity`, `evidence`, `unverifiedItems`, `warnings`) |
| `scripts/checklist_spec.py` | init, verify (imported) | Canonical spec + specHash |
| `scripts/dispatch.sh` | coordinator | One-line dispatch: task-create, init-checklist, contract, dispatch, brief, send, rename, state entry with `spec_hash`; refuses no/over-8 Done-when |
| `scripts/settle.sh` | coordinator | Gate with the state's `spec_hash`, entry status `settled`/`verify_failed`, `--rename-back` |
| `scripts/orch_state.py` | dispatch.sh, settle.sh | Locked state-file updates, Orca id parsing, Done-when parsing, `gate-snapshot` / `gate-check` |

### Gate snapshot

Workers run as the same OS user, often in bypass mode, so a worker can edit `verify-checklist.py` or `orca-done.sh` in the skill directory and make its own gate pass. `dispatch.sh` freezes the gate scripts (`GATE_FILES` in `orch_state.py`: verify-checklist, checklist_spec, orch_state, settle, dispatch, orca-done, mark-item, write-report, init-checklist) into `<state dir>/gate/<sha256 of their hashes>/`, files 0444, directory 0555, and records `gate_hash` on the dispatch entry. Trust is taken on first use per state file; a later dispatch whose skill scripts differ stops with exit 4 until the coordinator passes `--accept-gate-change`.

`settle.sh` runs from that copy (it re-executes itself there when started from the skill dir) and before the checklist gate checks: (1) the copy still hashes to `gate_hash` (else `modified after dispatch`), (2) the skill dir's gate scripts equal the copy (else the worker-side `orca-done.sh` ran an edited gate). Either one fails the Task; `--allow-gate-change` turns (2) into a warning when you changed the skill yourself. A dispatch entry without `gate_hash` fails.

Limit: this stops a worker that edits the gate it is told to run, not a same-user attacker that rewrites the frozen copy, the state file and their hashes together. For that, run workers as another OS user or in a sandbox that cannot write `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/`.
| `scripts/tests/` | maintainers | Regression suite: `python3 -m pytest scripts/tests -q` (or `python3 -m unittest discover -s scripts/tests`) |
| `scripts/orca-done.sh` | worker | The only completion path: gate, then `worker_done` with DONE-WHEN lines built from the checklist |
| `scripts/install-worker-contract.sh` | coordinator | Install/refresh `<pane>/.orca/worker-contract.md` once per pane (`--pane`, `--skill`, `--family`, `--force`) |
| `scripts/claude-stop-hook.sh` | Claude only, optional | Stop hook that refuses to end the turn while the worker's checklist is open |

## 4. One concern per Task, and a verify Task

- One active concern per Task; more than ~8 observable steps means more Tasks chained with `--deps`.
- For risky or multi-step work add `verify-<title>`: a Task that depends on the implement Task (`--deps '["<implement task id>"]'`), dispatched to a different idle terminal when one exists (any agent type), with its own checklist: rerun the acceptance commands, run `verify-checklist.py --checklist <implement checklist> --expect-spec-hash <implement spec_hash> --protected-diff --rerun-acceptance` (the coordinator puts the hash in its spec), read the diff against Ownership and Done-when, and report findings with file:line. Its spec says: "Do not trust the implement worker's worker_done body or report; check the tree yourself. Do not fix anything; report."
- A verify Task that fails sends the implement Task to retry (same rules as a failed Task).

## 5. Protected paths, no test tampering

Every spec lists Protected paths, and the checklist carries them. The worker must not edit, delete, skip or weaken tests, snapshots, fixtures or CI config to make acceptance pass (no `.skip`, `xit`, `@Ignore`, lowered thresholds, deleted assertions). If a protected file really must change, the worker `ask`s; the coordinator answers and the worker then passes `--allow-protected <path>` to `orca-done.sh` and names it in the summary. Several workers share one worktree, and `--protected-diff` sees the whole worktree, so another worker's change can trip the check (a false fail). Rule <!-- anti-skip:parallel-wave -->: a wave runs Tasks in parallel only when their Ownership and Protected paths do not overlap; otherwise serialize them with `--deps`. When a hit still happens, the coordinator judges it from `git log` before retrying. (Filtering by a worker-reported `touched[]` list is not used: that list is untrusted.)

## 6. Report file vs short body

- `worker_done` body: DONE-WHEN lines (`N. done: <evidence>` / `N. skipped: <reason>`), `progress k/N`, `gate`, `checklist`, a 2-sentence summary, `report: <path>`. `orca-done.sh` builds it from the checklist; the worker cannot add a `done` the file does not have.
- `.orca/tasks/<taskId>/report.json`: per-step commands, exit codes, notes, output tails, performance checklist results. Shape: `{taskId, dispatchId, steps:[{id, commands:[], exitCode, notes}], summary}`.

## 7. Heartbeat `step k/N`

Every heartbeat names the item being worked: `--subject "step <k>/<N>: <item text, short>"`. A heartbeat that stays on the same step for three beats, or jumps to N/N with no `mark-item` evidence in the checklist, is a signal to `terminal read` that worker.

## 8. Start ritual (first actions of every worker)

Stated in the worker contract; the brief does not repeat it.

1. `pwd` (must be the pane path) and `git status --short`, `git log --oneline -3` (branch and base).
2. Read the checklist: `cat <checklist path>`; restate the Original goal and the N items in one line each.
3. Acceptance smoke: run the first acceptance command once (or `--help`/dry run when it is slow) so a broken environment is reported before coding, not after.
4. Heartbeat `step 0/N: start ritual done`.

## 9. Original goal and scope in every spec

Every spec carries: Original goal (the user's words, one line), In scope, Out of scope, Done-when (numbered), Protected paths, Checklist path, Report path, Start ritual, Heartbeat `step k/N`, Completion via `orca-done.sh`. Out of scope is explicit, so "I also refactored X" is a verification issue, not a bonus.

## 10. Coordinator verify (always)

After `worker_done`, even with `gate: passed`:

1. `bash <state dir>/gate/<entry gate_hash>/settle.sh --from $SELF --task <taskId> --rename-back` (frozen copy, see "Gate snapshot"): gate integrity first, then `verify-checklist.py --expect-spec-hash <state spec_hash> --protected-diff --rerun-acceptance --require-report <report> --cwd <pane>` and the state entry update. Never run the gate without `--expect-spec-hash`. Read `unverifiedItems` (plain-text evidence) and check those items yourself.
2. Body checks: every item number present; `gate:` line present. A `worker_done` with no `DONE-WHEN:`/`gate:` lines was sent raw, bypassing the gate: treat it as unverified, run step 1 yourself, and treat a failure as the first failed attempt. `gate: SKIPPED` is the same.
3. Diff vs Ownership, In/Out of scope and Protected paths; evidence vs actual commits and test output.
4. Prefer a fresh `verify-<title>` Task on another idle terminal for multi-step or risky work (section 4).

## 11. Agent notes

| Agent | Hook gate | What enforces completion |
|---|---|---|
| claude | Optional Stop hook (`references/claude-stop-hook.example.json` + `scripts/claude-stop-hook.sh`) | orca-done.sh + coordinator verify; the hook only adds a nudge |
| grok | none | orca-done.sh + coordinator verify |
| codex | none | orca-done.sh + coordinator verify. A sandboxed Codex may not write `.git`; it still writes `.orca/` in the worktree. If it cannot run `orca` at all, it falls back to the FB block (`--fb-nonce`) |
| agy | none | orca-done.sh + coordinator verify |

Claude Stop hook (optional) in short: it reads the hook's stdin, exits 0 when `stop_hook_active` is true (no loops), finds checklists under `<repo>/.orca/tasks/*/` whose `worker` equals `$ORCA_TERMINAL_HANDLE` and that have no `done.json`, and exits 2 with the misses on stderr so Claude keeps working. Settings fragment:

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash <SKILL_DIR>/scripts/claude-stop-hook.sh", "timeout": 120}]}]}}
```

Grok, Codex and agy have no equivalent; never make a Task depend on a hook.

## 12. Branch A (agent ignores worker_done) and FB nonce

Branch A agents still use `orca-done.sh`. With a Task and Dispatch they call it normally. Without a Dispatch (Branch A skips Task/Dispatch), the brief's completion line is:

```
bash SKILL/scripts/orca-done.sh --task <label> --checklist <path> --fb-nonce <nonce> --summary "<2 sentences>" [--outcome failed]
```

The script runs the same gate and then prints `FB-START ... FB-<nonce>-END` on stdout instead of calling `orca`; the brief never contains the closing line literally, so the coordinator's poll for `FB-<nonce>-END` still proves the agent ran it. If the agent ignores both the script and the block, Branch B rules in SKILL.md apply (positive proof before abandon).
