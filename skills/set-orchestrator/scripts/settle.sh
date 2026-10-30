#!/usr/bin/env bash
# Coordinator gate after worker_done: verify one Task against the TRUSTED specHash in the state file.
#
# Usage:
#   settle.sh --task <task id> [--from <self handle> | --state <state file>] [--rename-back]
#             [--allow-protected <path>]... [--no-acceptance] [--no-report] [--orca <exe>]
#             [--allow-gate-change]
#
# Reads dispatches[] entry <task id> from ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self>.json,
# runs verify-checklist.py --checklist <entry.checklist> --expect-spec-hash <entry.spec_hash>
# --protected-diff --rerun-acceptance --cwd <entry.pane> [--require-report <entry.report>],
# then sets the entry's status to "settled" (gate passed) or "verify_failed", settled time and a verify summary
# (errors, warnings, unverifiedItems). A missing spec_hash in the entry is a failure, never a skip.
# --rename-back renames the worker tab back to entry.base_title (its roster title).
# Gate integrity: the entry's gate_hash names the frozen copy <state dir>/gate/<hash>/ made by
# dispatch.sh. Run THAT copy (dispatch.sh prints its path as "settle"); this script re-executes
# itself from there when started from the skill dir. Before verifying it checks that the copy
# still matches its hash and that the skill dir's gate scripts (which the worker's orca-done.sh
# ran) equal the copy; either difference fails the Task (--allow-gate-change turns the second
# into a warning when you changed the skill yourself). A missing gate_hash is a failure.
# Exit: verify-checklist's exit code (0 pass, 2 fail); 2 also for bad input.
# Not done here: reading the diff against Ownership, the performance checklist and skill use
# (SKILL.md loop step 5) and the reuse/retain/release accounting (step 6).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ST="$HERE/orch_state.py"
TASK=""; SELF="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"; STATE=""; RENAME=0; ACCEPT=1; REPORT_REQ=1; ORCA="${ORCA_CLI_COMMAND:-orca}"
ALLOW=(); ALLOW_GATE=()
ORIG_ARGS=("$@")
die() { echo "settle.sh: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --task) TASK="${2:-}"; shift 2 ;;
    --from) SELF="${2:-}"; shift 2 ;;
    --state) STATE="${2:-}"; shift 2 ;;
    --rename-back) RENAME=1; shift ;;
    --allow-protected) ALLOW+=(--allow-protected "${2:-}"); shift 2 ;;
    --no-acceptance) ACCEPT=0; shift ;;
    --no-report) REPORT_REQ=0; shift ;;
    --orca) ORCA="${2:-}"; shift 2 ;;
    --allow-gate-change) ALLOW_GATE=(--allow-gate-change); shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$TASK" ] || die "--task is required"
if [ -z "$STATE" ]; then
  [ -n "$SELF" ] || die "--from <self handle> or --state is required outside an Orca terminal"
  STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${SELF//:/_}.json"  # chat addresses contain ":"
fi
[ -f "$STATE" ] || die "state file not found: $STATE"
python3 "$ST" get "$STATE" "$TASK" >/dev/null || die "no dispatches[] entry for $TASK in $STATE"
field() { python3 "$ST" get "$STATE" "$TASK" "$1" 2>/dev/null || true; }
GATE_HASH="$(field gate_hash)"
if [ -n "$GATE_HASH" ]; then
  GATE_DIR="$(cd "$(dirname "$STATE")" && pwd)/gate/$GATE_HASH"
  if [ "$HERE" != "$GATE_DIR" ] && [ -f "$GATE_DIR/settle.sh" ] && [ -z "${ORCH_SETTLE_FROZEN:-}" ]; then
    ORCH_SETTLE_FROZEN=1 exec bash "$GATE_DIR/settle.sh" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"} --state "$STATE"
  fi
fi
HASH="$(field spec_hash)"; CHECKLIST="$(field checklist)"; PANE="$(field pane)"; REPORT="$(field report)"
TERM_H="$(field terminal)"; BASE="$(field base_title)"
[ -n "$PANE" ] || PANE="$(python3 "$ST" field "$STATE" pane_path 2>/dev/null || true)"
[ -n "$CHECKLIST" ] || CHECKLIST="$PANE/.orca/tasks/$TASK/checklist.json"

args=(--checklist "$CHECKLIST" --protected-diff --cwd "$PANE")
[ "$ACCEPT" = 1 ] && args+=(--rerun-acceptance)
[ "$REPORT_REQ" = 1 ] && [ -n "$REPORT" ] && args+=(--require-report "$REPORT")
[ ${#ALLOW[@]} -gt 0 ] && args+=("${ALLOW[@]}")
GATE_JSON='null'
if [ -z "$GATE_HASH" ]; then
  OUT='{"ok": false, "errors": ["state entry has no gate_hash: the gate scripts were not frozen at dispatch; re-dispatch with dispatch.sh"]}'
  RC=2
  echo "settle.sh: FAIL - no gate_hash for $TASK in $STATE; acceptance not run" >&2
elif ! GATE_JSON="$(python3 "$ST" gate-check "$STATE" "$GATE_HASH" ${ALLOW_GATE[@]+"${ALLOW_GATE[@]}"})"; then
  OUT="$(python3 -c 'import json,sys; g=json.loads(sys.argv[1]); print(json.dumps({"ok": False, "specIntegrity": "not checked", "errors": ["gate integrity: "+e for e in g.get("errors", [])]}))' "$GATE_JSON")"
  RC=2
  echo "settle.sh: FAIL - gate scripts changed; checklist not trusted, acceptance not run" >&2
elif [ -z "$HASH" ]; then
  OUT='{"ok": false, "errors": ["state entry has no spec_hash: cannot trust the checklist; re-dispatch with dispatch.sh"]}'
  RC=2
  echo "settle.sh: FAIL - no spec_hash for $TASK in $STATE; acceptance not run" >&2
else
  OUT="$(python3 "$HERE/verify-checklist.py" "${args[@]}" --expect-spec-hash "$HASH")"; RC=$?
fi

STATUS="settled"; [ "$RC" = 0 ] || STATUS="verify_failed"
ORCH_HERE="$HERE" python3 - "$STATE" "$TASK" "$STATUS" "$OUT" "$GATE_JSON" <<'PY' || echo "settle.sh: WARNING could not update state" >&2
import json, sys, time, os
state, task, status, out, gate = sys.argv[1:6]
try:
    v = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
except Exception:
    v = {"raw": out[-500:]}
patch = {"task_id": task, "status": status, "settled": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "verify": {k: v.get(k) for k in ("ok", "specIntegrity", "passed", "total", "errors", "warnings",
                                           "unverifiedItems", "protectedViolations", "acceptance")}}
try:
    g = json.loads(gate)
except Exception:
    g = None
if isinstance(g, dict):
    patch["verify"]["gate"] = {k: g.get(k) for k in ("ok", "hash", "errors", "warnings")}
sys.path.insert(0, os.environ["ORCH_HERE"])
import orch_state
orch_state.upsert(state, patch)
PY
printf '%s\n' "$OUT"
if [ "$RENAME" = 1 ] && [ -n "$TERM_H" ] && [ -n "$BASE" ]; then
  "$ORCA" terminal rename --terminal "$TERM_H" --title "$BASE" --json >/dev/null 2>&1 || echo "settle.sh: WARNING rename failed" >&2
fi
exit "$RC"
