#!/usr/bin/env bash
# Give one idle Claude worker a fresh context: handoff to disk, then /clear (the scripted form of
# "Handoff, then clear" in references/model-routing.md). Coordinator-side helper, not part of the gate.
#
# Usage:
#   fresh-worker.sh --terminal <handle> [--state <file>] [--pane <path>] [--from <self handle>] [--orca <exe>] \
#     [--model <name>] [--effort <level>] [--timeout-ms N] [--dry-run]
#
# State file, pane, self handle and the Orca executable resolve exactly as in dispatch.sh.
# Steps: refuse when the terminal has an open Dispatch (dispatched or active) in the state file or is
# not idle (terminal wait --for tui-idle, 10 s) -> send the handoff request with terminal send --enter
# (write plans/handoffs/session-handoff-<handle>.md as the Context section of .orca/worker-contract.md
# says, then stop) -> wait for tui-idle (--timeout-ms, default 300000) -> require that
# <pane>/plans/handoffs/session-handoff-<handle>.md exists and is not older than the moment the request
# was sent (one second resolution) -> no fresh file: ask once more and wait again; still none: exit 5
# and /clear is NOT sent -> send /clear, wait for tui-idle -> with --model: send /model <name> (and
# /effort <level> when --effort is given), wait for tui-idle, write model and effort into the worker's
# roster[] entry (orch_state.py roster-set) -> record orch_state.py context-reset (time and handoff path)
# -> print one JSON line {ok, terminal, handoff, model, effort}.
# --model and --effort accept only letters, digits and . _ [ ] - (the rule open-agents.sh applies to model
# ids); --effort needs --model. Only Claude workers understand these commands; check the agent first.
# --dry-run prints the steps and sends nothing (the state-file checks still run).
# Exit: 0 done; 2 bad input; 1 an Orca step failed or the roster entry could not be updated (a reset that
# already happened is still recorded); 3 the terminal has an open Dispatch (nothing sent); 4 the terminal
# is not idle (nothing sent); 5 no fresh handoff file after two requests (no /clear sent).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ST="$HERE/orch_state.py"
TO=""; STATE=""; PANE=""; ORCA=""; MODEL=""; EFFORT=""; WAIT_MS=300000; DRY=0
SELF="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"
IDLE_MS=120000; PROBE_MS=10000
die() { echo "fresh-worker.sh: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --terminal) TO="${2:-}"; shift 2 ;;
    --state) STATE="${2:-}"; shift 2 ;;
    --pane) PANE="${2:-}"; shift 2 ;;
    --from) SELF="${2:-}"; shift 2 ;;
    --orca) ORCA="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --effort) EFFORT="${2:-}"; shift 2 ;;
    --timeout-ms) WAIT_MS="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done

# ---- resolve ORCA (SKILL.md section 0) and the state file, as dispatch.sh does ----
if [ -z "$ORCA" ]; then
  if [ -n "${ORCA_CLI_COMMAND:-}" ]; then ORCA="$ORCA_CLI_COMMAND"
  elif [ -n "${ORCA_DEV_REPO_ROOT:-}" ]; then ORCA="orca-dev"
  elif [ "$(uname -s)" = Linux ] && [ -z "${ORCA_TERMINAL_HANDLE:-}" ] && command -v orca-ide >/dev/null 2>&1; then ORCA="orca-ide"
  else ORCA="orca"; fi
fi
if [ -z "$STATE" ]; then
  [ -n "$SELF" ] || die "--from <self handle> or --state is required outside an Orca terminal"
  STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${SELF//:/_}.json"  # chat addresses contain ":"
fi
[ -f "$STATE" ] || die "state file not found: $STATE (run '/set-orchestrator on' first)"
[ -n "$SELF" ] || SELF="$(python3 "$ST" field "$STATE" self_handle 2>/dev/null || true)"
[ -n "$PANE" ] || PANE="$(python3 "$ST" field "$STATE" pane_path 2>/dev/null || true)"
[ -n "$PANE" ] && [ -d "$PANE" ] || die "pane path not found: '$PANE' (--pane or state pane_path)"

# ---- validate input ----
[ -n "$TO" ] || die "--terminal <handle> is required"
case "$TO" in */*|*..*|*[[:space:]]*) die "bad terminal handle '$TO'" ;; esac
case "$WAIT_MS" in ''|*[!0-9]*) die "--timeout-ms must be a positive integer (got '$WAIT_MS')" ;; esac
[ "$WAIT_MS" -ge 1 ] || die "--timeout-ms must be a positive integer (got '$WAIT_MS')"
safe_id() { case "$1" in ''|*[!A-Za-z0-9._\[\]-]*) return 1 ;; esac; }  # same rule as open-agents.sh
if [ -n "$MODEL" ]; then safe_id "$MODEL" || die "bad --model '$MODEL' (letters, digits and . _ [ ] - only)"; fi
if [ -n "$EFFORT" ]; then
  [ -n "$MODEL" ] || die "--effort needs --model"
  safe_id "$EFFORT" || die "bad --effort '$EFFORT' (letters, digits and . _ [ ] - only)"
fi
REL="plans/handoffs/session-handoff-$TO.md"
HANDOFF="$PANE/$REL"
REQUEST="Write your session handoff now to $REL, as the Context section of .orca/worker-contract.md says, then stop."

# ---- refuse while the terminal has an open Dispatch ----
OPEN="$(python3 "$ST" open-dispatches "$STATE" "$TO")"
if [ -n "$OPEN" ]; then
  echo "fresh-worker.sh: $TO still has open dispatches; nothing sent. Settle or abandon them first:" >&2
  printf '%s\n' "$OPEN" | sed 's/^/  /' >&2
  exit 3
fi

if [ "$DRY" = 1 ]; then
  echo "DRY RUN ($ORCA, state $STATE, pane $PANE):"
  echo "  $ORCA terminal wait --terminal $TO --for tui-idle --timeout-ms $PROBE_MS --json   (refuse when not idle)"
  echo "  $ORCA terminal send --terminal $TO --text \"$REQUEST\" --enter --wait-submit 15 --json"
  echo "  $ORCA terminal wait --terminal $TO --for tui-idle --timeout-ms $WAIT_MS --json; require a fresh $HANDOFF (ask once more, else stop before /clear)"
  echo "  $ORCA terminal send --terminal $TO --text \"/clear\" --enter --wait-submit 15 --json; wait for tui-idle"
  if [ -n "$MODEL" ]; then
    echo "  $ORCA terminal send --terminal $TO --text \"/model $MODEL\" --enter ...${EFFORT:+; /effort $EFFORT}; wait for tui-idle; python3 $ST roster-set $STATE $TO --model $MODEL${EFFORT:+ --effort $EFFORT}"
  fi
  echo "  python3 $ST context-reset $STATE $TO --handoff $HANDOFF"
  exit 0
fi

orca_json() { # run an orca command, print its stdout; fail on exit != 0 or ok:false
  local out rc err
  err="$(mktemp)"
  out="$("$ORCA" "$@" 2>"$err")"; rc=$?
  if [ $rc -eq 0 ] && ! printf '%s' "$out" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
r=d.get("result") if isinstance(d,dict) else None
bad=(isinstance(d,dict) and d.get("ok") is False) or (isinstance(r,dict) and r.get("state")=="failed")
sys.exit(1 if bad else 0)'; then rc=1; fi
  if [ $rc -ne 0 ]; then
    echo "fresh-worker.sh: '$ORCA $1 $2' failed (exit $rc): $(cat "$err") $out" >&2
    rm -f "$err"; return 1
  fi
  rm -f "$err"; printf '%s' "$out"
}
send() { orca_json terminal send --terminal "$TO" --text "$1" --enter --wait-submit 15 --json >/dev/null; }
wait_idle() { orca_json terminal wait --terminal "$TO" --for tui-idle --timeout-ms "$1" --json >/dev/null; }
fresh_since() { python3 -c 'import os,sys; p=sys.argv[1]; sys.exit(0 if os.path.isfile(p) and os.path.getmtime(p) >= int(sys.argv[2]) else 1)' "$HANDOFF" "$1"; }

# ---- 1. the terminal must be idle ----
if ! wait_idle "$PROBE_MS" 2>/dev/null; then
  echo "fresh-worker.sh: $TO is not idle (no tui-idle within $((PROBE_MS / 1000)) s); nothing sent." >&2
  exit 4
fi

# ---- 2-4. handoff request, wait, require a file written after the request (ask twice) ----
GOT=0
for attempt in 1 2; do
  SENT="$(python3 -c 'import time; print(int(time.time()))')"
  send "$REQUEST" || exit 1
  wait_idle "$WAIT_MS" || exit 1
  if fresh_since "$SENT"; then GOT=1; break; fi
  [ "$attempt" = 2 ] || echo "fresh-worker.sh: no fresh $HANDOFF after the request; asking once more." >&2
done
if [ "$GOT" = 0 ]; then
  echo "fresh-worker.sh: no fresh handoff at $HANDOFF after two requests; /clear NOT sent. Leave $TO as it is, use another worker, and tell the user." >&2
  exit 5
fi

# ---- 5. /clear ----
send "/clear" || exit 1
wait_idle "$IDLE_MS" || exit 1

# The context is gone now: record the reset before the model switch, so a failed switch does not
# make a rerun ask for a second handoff and clear.
RC=0
python3 "$ST" context-reset "$STATE" "$TO" --handoff "$HANDOFF" || { echo "fresh-worker.sh: WARNING could not record the reset in $STATE" >&2; RC=1; }

# ---- 6. model switch ----
if [ -n "$MODEL" ]; then
  send "/model $MODEL" || exit 1
  if [ -n "$EFFORT" ]; then send "/effort $EFFORT" || exit 1; fi
  wait_idle "$IDLE_MS" || exit 1
  python3 "$ST" roster-set "$STATE" "$TO" --model "$MODEL" ${EFFORT:+--effort "$EFFORT"} \
    || { echo "fresh-worker.sh: WARNING roster entry of $TO not updated; fix model and effort in $STATE" >&2; RC=1; }
else
  MODEL="$(python3 "$ST" worker-info "$STATE" "$TO" model 2>/dev/null || true)"
fi
[ -n "$EFFORT" ] || EFFORT="$(python3 "$ST" worker-info "$STATE" "$TO" effort 2>/dev/null || true)"

# ---- 7. report ----
python3 -c 'import json,sys; print(json.dumps(dict(zip(["ok","terminal","handoff","model","effort"], [sys.argv[1]=="0"]+sys.argv[2:]))))' \
  "$RC" "$TO" "$HANDOFF" "$MODEL" "$EFFORT"
exit "$RC"
