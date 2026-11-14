#!/usr/bin/env bash
# OPTIONAL, Claude Code orchestrator session only. SessionStart hook (matchers: compact, resume).
#
# After /compact or a resumed session the model forgets it is the orchestrator. This hook prints a
# short reminder to stdout, which Claude Code adds to the session context, but only while the Run
# is open: the state file ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self-handle>.json
# exists (`/set-orchestrator off` deletes it). Self handle: $ORCA_TERMINAL_HANDLE, else
# orca_session_id:$ORCA_AGENT_SESSION_ID (':' becomes '_' in the file name).
# Prints nothing and exits 0 when there is no handle, no readable state file, or no run_id.
# It never fails the session. Read from the state file: run_id, pane_path, the roster (title,
# agent, model, effort, handle of each worker, so the session still knows its workers) and the
# dispatches still awaiting settlement (status dispatched or active: task id, worker, tier).
# Then the closing rules a compaction summary tends to lose, the exact worklog.py search command
# (absolute path, so it survives without the skill text) and the last Tasks of the pane with their
# results (worklog.py recent), so the session still knows what it already did.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ME="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"
[ -n "$ME" ] || exit 0
STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${ME//:/_}.json"
[ -r "$STATE" ] || exit 0
python3 - "$STATE" "$HERE" 2>/dev/null <<'PY' || true
import json, re, sys

path, here = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    if not isinstance(st, dict):
        sys.exit(0)

    def clean(v):  # one line, no control characters, bounded
        return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()[:200]

    run, pane = clean(st.get("run_id")), clean(st.get("pane_path"))
    if not run:
        sys.exit(0)
    def short(v, n=80):
        return clean(v)[:n]

    open_d = [d for d in st.get("dispatches") or []
              if isinstance(d, dict) and d.get("status") in ("dispatched", "active")]
    lines = [f"ORCHESTRATOR ROLE ACTIVE. This session is the ORCHESTRATOR for Run {run} in pane {pane}. "
             f"State file: {clean(path)}. Dispatches not yet settled: {len(open_d)}. "
             "Do not edit code yourself; delegate every Task to the pane's workers. "
             "Next: run `/set-orchestrator status`, then continue the coordinator loop."]
    roster = [r for r in st.get("roster") or [] if isinstance(r, dict) and r.get("handle")]
    if roster:
        lines.append(f"Your workers in this pane ({len(roster)}); they are still open, do not open new ones:")
        for r in roster[:12]:
            model = "/".join(x for x in (short(r.get("model"), 30), short(r.get("effort"), 12))
                             if x and x != "unknown") or "model unknown"
            lines.append(f"- {short(r.get('title'), 30) or '?'} ({short(r.get('agent'), 20) or '?'}, {model}): "
                         f"{short(r.get('handle'))}")
        if len(roster) > 12:
            lines.append(f"- and {len(roster) - 12} more in the state file")
    if open_d:
        lines.append("Dispatches still open (wait for worker_done, then settle):")
        for d in open_d[:8]:
            lines.append(f"- {short(d.get('task_id'), 40)} on {short(d.get('base_title'), 30) or short(d.get('terminal'))}"
                         f" (tier {short(d.get('tier'), 12) or 'none'})")
        if len(open_d) > 8:
            lines.append(f"- and {len(open_d) - 8} more in the state file")
    lines.append("Closing rules: close a finished Task only with its frozen settle.sh (it runs the gate). "
                 "`orch_state.py abandon` gives a Task up and needs --user-said with the user's words; never "
                 "use it to close finished work. If settle.sh fails for a reason that is not the worker's, "
                 "stop and tell the user. --keep-context stops at 6 Tasks per worker context; then run "
                 "fresh-worker.sh.")
    lines.append("Work log: what you already asked for and what came of it is on disk. Before you ask the "
                 "user whether something exists or was done, before you write a spec, and before you forward "
                 "a worker's question, run: "
                 f"python3 '{here}/worklog.py' search <words> --state '{clean(path)}'  "
                 "(then `show <task id>`, the report, `git log` or the code). Ask the user only when those "
                 "are silent.")
    print("\n".join(lines))
except Exception:
    pass
PY
if [ -n "$(python3 "$HERE/orch_state.py" field "$STATE" run_id 2>/dev/null)" ]; then
  python3 "$HERE/worklog.py" recent -n 6 --state "$STATE" 2>/dev/null || true
fi
exit 0
