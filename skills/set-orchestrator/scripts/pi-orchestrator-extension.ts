// OPTIONAL. Pi extension for a Pi session that runs the set-orchestrator skill.
// Install: scripts/install-pi.sh copies this file to ${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/set-orchestrator.ts.
//
// Pi has no Claude Code hooks, so this file does their two jobs before every run of the agent:
//   1. Role: appends the output of orchestrator-resume-hook.sh (role, Run, workers, open dispatches,
//      closing rules, last Tasks) to the system prompt. The system prompt is rebuilt on every run, so
//      the role survives Pi's automatic compaction.
//   2. Lookup: runs orchestrator-ask-hook.sh on the user's prompt (the UserPromptSubmit part) and adds
//      the Tasks and commits it finds as a hidden message.
// It does nothing unless this Pi runs in an Orca terminal (ORCA_TERMINAL_HANDLE or
// ORCA_AGENT_SESSION_ID) and the Run's state file exists, so other Pi sessions are not changed.
// The skill directory is $SET_ORCHESTRATOR_SKILL_DIR, else <pi agent dir>/skills/set-orchestrator.
// Plain JavaScript on purpose (no type annotations), so `node --check` and a unit test can load it.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ROLE_TTL_MS = 120000;

export function skillDir(env = process.env) {
  if (env.SET_ORCHESTRATOR_SKILL_DIR) return env.SET_ORCHESTRATOR_SKILL_DIR;
  return join(env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent"), "skills", "set-orchestrator");
}

export function stateFile(env = process.env) {
  const me = env.ORCA_TERMINAL_HANDLE || (env.ORCA_AGENT_SESSION_ID ? `orca_session_id:${env.ORCA_AGENT_SESSION_ID}` : "");
  if (!me) return "";
  const base = env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude");
  return join(base, "orchestrator", me.replaceAll(":", "_") + ".json");
}

function run(script, input, env) {
  const path = join(skillDir(env), "scripts", script);
  if (!existsSync(path)) return "";
  const r = spawnSync("bash", [path], { input, encoding: "utf8", timeout: 20000, env });
  return r.status === 0 && typeof r.stdout === "string" ? r.stdout.trim() : "";
}

export function roleText(env = process.env) {
  const state = stateFile(env);
  if (!state || !existsSync(state)) return "";
  return run("orchestrator-resume-hook.sh", "{}", env);
}

export function lookupText(prompt, env = process.env) {
  const state = stateFile(env);
  if (!state || !existsSync(state) || typeof prompt !== "string" || !prompt.trim()) return "";
  return run("orchestrator-ask-hook.sh", JSON.stringify({ hook_event_name: "UserPromptSubmit", prompt }), env);
}

export default function (pi) {
  let cached = { at: 0, text: "" };
  pi.on("session_compact", async () => {
    cached = { at: 0, text: "" };
  });
  pi.on("before_agent_start", async (event) => {
    try {
      if (Date.now() - cached.at > ROLE_TTL_MS) cached = { at: Date.now(), text: roleText() };
      if (!cached.text) return undefined;
      const out = {
        systemPrompt:
          `${event.systemPrompt || ""}\n\n# set-orchestrator (this session)\n${cached.text}\n` +
          "In Pi the skill is /skill:set-orchestrator (on, status, off); read references/setup.md, section Pi, for what differs from Claude Code.",
      };
      const found = event.messageType === "user" ? lookupText(event.prompt) : "";
      if (found) out.message = { customType: "set-orchestrator-lookup", content: found, display: false };
      return out;
    } catch {
      return undefined;
    }
  });
}
