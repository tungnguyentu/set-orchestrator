# set-orchestrator

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill that turns the current Claude session into the standing orchestrator for the agents open in the same [Orca](https://orca.dev) pane. After `on`, the session stops writing code itself. It splits your requests into self-contained tasks, dispatches them to idle agents in that pane through `orca orchestration`, waits for `worker_done`, verifies the result, and reports.

Workers can be any TUI agent that Orca recognizes in the pane: Claude Code, Codex, Grok, Antigravity (`agy`), and others.

## Requirements

- Claude Code 2.1 or newer, running inside an Orca terminal.
- Orca 1.4.227 or newer, with the `orca` CLI on `PATH`. The skill uses `orca orchestration`, `orca terminal` and `orca status`.
- Python 3 and bash for the helper scripts.
- Optional: the agent CLIs you want the orchestrator to open for you (`claude`, `grok`, `codex`, `agy`).
- Optional: the `ak:*` skills from AgentKit (`/ak:cook`, `/ak:debug`, `/ak:fix`, `/ak:code-review`, `/ak:handoff`). The worker brief names them. If they are not installed, workers say so and work without them, and the orchestrator skips the handoff file before `/compact`.
- Optional: a `cc-launch` wrapper at `~/.local/bin/cc-launch`. When present, Claude workers opened with `--open` use it. When absent, they start with plain `claude`.

## Install

Clone into your user-scope skills directory. Claude Code loads skills from `~/.claude/skills`. If you use `CLAUDE_CONFIG_DIR`, clone into `$CLAUDE_CONFIG_DIR/skills` instead.

```bash
git clone https://github.com/tungnguyentu/set-orchestrator.git ~/.claude/skills/set-orchestrator
```

Start a new Claude Code session inside an Orca terminal. The skill appears as `/set-orchestrator`.

To update later:

```bash
git -C ~/.claude/skills/set-orchestrator pull
```

## Usage

Open the agents you want as workers in the same Orca pane as the Claude session, or let the skill open them. Then, in that Claude session:

```text
/set-orchestrator on "<objective>"
/set-orchestrator on "<objective>" --open claude:2,grok,codex,agy
/set-orchestrator status
/set-orchestrator off
```

You can also say it in words: "set this session as orchestrator", "làm orchestrator", "điều phối các agent trong pane", "mở thêm 2 claude và 1 grok".

What `on` does:

1. Confirms Orca is up and finds this session's own terminal handle and pane.
2. Binds or creates an Orca orchestration Run.
3. With `--open`, creates one terminal per requested agent in the same pane and starts it with permission prompts disabled (Claude `--dangerously-skip-permissions`, Grok `--permission-mode bypassPermissions`, Codex `--dangerously-bypass-approvals-and-sandbox`, Antigravity `--dangerously-skip-permissions`).
4. Lists the eligible workers in the pane and writes a state file so the role survives compaction or a new session.
5. Sets a 300k auto-compact window on every Claude worker and on itself.
6. Renames the tabs: the orchestrator becomes `orchestrator`, workers become `<agent>-<n>`. While a worker is busy, its tab shows the task title.

From then on, every implementation request goes through the dispatch loop: write a spec, dispatch to an idle worker with a short brief, wait for `worker_done`, verify the commit or diff, retry a failed task once, and send one final report. Questions from workers are forwarded to you one at a time.

`off` asks what to do with the terminals it opened, deletes the state file, and removes the role. It never resets the Orca Run.

## Files

| Path | Purpose |
|------|---------|
| `SKILL.md` | The skill itself: role rules, `on`, `status`, `off`, the coordinator loop, fallbacks, pitfalls. |
| `references/coordinator-loop.md` | The detailed loop, the worker brief template, and the error table. |
| `scripts/self-handle.sh` | Prints this session's Orca terminal handle. |
| `scripts/pane-roster.py` | Lists the terminals in a pane and marks which ones are eligible workers. Resolves a worker by tab title. |
| `scripts/open-agents.sh` | Opens agent terminals in a pane with permission prompts disabled and waits until Orca reports the agent identity. |

State file: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<terminal-handle>.json`.

## Safety notes

- The orchestrator only sends work to terminals whose worktree path equals its own pane. Orca does not enforce this, the skill does.
- It never sends to a busy terminal, never opens a terminal unless you ask, and never runs `orca orchestration reset`.
- Agents opened with `--open` run with permission prompts disabled. Use this only in a checkout you are willing to let an agent change without confirmation.
- Workers are told to commit but never push or deploy.

## License

MIT, see `LICENSE`.
