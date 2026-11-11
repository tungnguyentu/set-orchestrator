#!/usr/bin/env bash
# OPTIONAL, Claude Code orchestrator session only. SessionStart hook (matchers: compact, resume).
#
# After /compact or a resumed session the model forgets it is the orchestrator. This hook prints a
# short reminder to stdout, which Claude Code adds to the session context, but only while the Run
# is open: the state file ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self-handle>.json
# exists (`/set-orchestrator off` deletes it). Self handle: $ORCA_TERMINAL_HANDLE, else
# orca_session_id:$ORCA_AGENT_SESSION_ID (':' becomes '_' in the file name).
# Prints nothing and exits 0 when there is no handle, no readable state file, or no run_id.
# It never fails the session. Only run_id, pane_path and the count of dispatches still awaiting
# settlement (status dispatched or active) are read from the state file.
ME="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"
[ -n "$ME" ] || exit 0
STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${ME//:/_}.json"
[ -r "$STATE" ] || exit 0
python3 - "$STATE" 2>/dev/null <<'PY' || true
import json, re, sys

path = sys.argv[1]
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
    open_n = sum(1 for d in st.get("dispatches") or []
                 if isinstance(d, dict) and d.get("status") in ("dispatched", "active"))
    print(f"ORCHESTRATOR ROLE ACTIVE. This session is the ORCHESTRATOR for Run {run} in pane {pane}. "
          f"State file: {clean(path)}. Dispatches not yet settled: {open_n}. "
          "Do not edit code yourself; delegate every Task to the pane's workers. "
          "Next: run `/set-orchestrator status`, then continue the coordinator loop.")
except Exception:
    pass
PY
exit 0
