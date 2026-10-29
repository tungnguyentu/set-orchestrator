#!/usr/bin/env bash
# One-line dispatch of one Task to one idle worker terminal (compact brief path).
#
# Usage:
#   dispatch.sh --to <terminal> --title "<task title>" --spec-file <spec.md> \
#     --goal "<original goal>" --change "<1-2 sentences>" --ownership "<paths>" \
#     --acceptance "<cmd>" [--acceptance ...] [--protected <glob>]... \
#     [--out-of-scope "<short>"] [--item "<Done-when N>"]... [--task <existing task id>] \
#     [--deps '<json array>'] [--from <self handle>] [--state <state file>] [--pane <path>] \
#     [--agent <type>] [--agent-title <claude-1>] [--wave N] [--attempt N] \
#     [--family ak|ce|superpowers|none] [--orca <exe>] [--force-checklist] [--resend] [--accept-gate-change] [--dry-run]
#
# Steps: task-create (unless --task) -> init-checklist.py (specHash + coordinator-side spec copy
# in <state dir>/specs/<task>.json) -> install-worker-contract.sh (no-op when current) ->
# orchestration dispatch (no --inject) -> state entry -> short brief (<state dir>/briefs/<task>.txt,
# Finish line carries --expect-spec-hash) -> terminal send -> tab rename -> state entry "active".
# Done-when items come from --item, else from the spec's numbered "Done-when" list; none or more
# than 8 = exit 2 before anything is created.
# Re-running for the same --task is safe: an existing checklist whose specHash equals the state
# entry's spec_hash is reused, and an existing dispatch_id is reused instead of dispatching again.
# A Task whose state entry is already "active" (brief sent) is not sent twice: exit 3, unless
# --resend (for example after the worker's /clear lost the brief).
# Gate snapshot: before anything is created, the gate scripts are frozen (read-only) into
# <state dir>/gate/<hash>/ via orch_state.py gate-snapshot (trust on first use per state file);
# the entry records gate_hash and the output JSON prints the frozen settle.sh to use later. If the
# skill's scripts changed since the last snapshot it stops with exit 4, unless
# --accept-gate-change (only when you changed them yourself).
# Exit: 0 dispatched and sent; 4 gate scripts changed (nothing created); 2 bad input; 3 already active (nothing sent); 1 an Orca step failed.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
ST="$HERE/orch_state.py"
TO=""; TITLE=""; SPEC_FILE=""; GOAL=""; CHANGE=""; OWN=""; OOS="see Task spec"; TASK=""; DEPS=""
SELF="${ORCA_TERMINAL_HANDLE:-}"; FROM_GIVEN=0; STATE=""; PANE=""; AGENT=""; AGENT_TITLE=""
WAVE=1; ATTEMPT=1; FAMILY=""; ORCA=""; FORCE_CL=0; DRY=0; RESEND=0; ACCEPT_GATE=()
ITEMS=(); PROT=(); ACC=()
die() { echo "dispatch.sh: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO="${2:-}"; shift 2 ;;
    --title) TITLE="${2:-}"; shift 2 ;;
    --spec-file) SPEC_FILE="${2:-}"; shift 2 ;;
    --goal) GOAL="${2:-}"; shift 2 ;;
    --change) CHANGE="${2:-}"; shift 2 ;;
    --ownership) OWN="${2:-}"; shift 2 ;;
    --out-of-scope) OOS="${2:-}"; shift 2 ;;
    --item) ITEMS+=("${2:-}"); shift 2 ;;
    --protected) PROT+=("--protected" "${2:-}"); shift 2 ;;
    --acceptance) ACC+=("--acceptance" "${2:-}"); shift 2 ;;
    --task) TASK="${2:-}"; shift 2 ;;
    --deps) DEPS="${2:-}"; shift 2 ;;
    --from) SELF="${2:-}"; FROM_GIVEN=1; shift 2 ;;
    --state) STATE="${2:-}"; shift 2 ;;
    --pane) PANE="${2:-}"; shift 2 ;;
    --agent) AGENT="${2:-}"; shift 2 ;;
    --agent-title) AGENT_TITLE="${2:-}"; shift 2 ;;
    --wave) WAVE="${2:-}"; shift 2 ;;
    --attempt) ATTEMPT="${2:-}"; shift 2 ;;
    --family) FAMILY="${2:-}"; shift 2 ;;
    --orca) ORCA="${2:-}"; shift 2 ;;
    --force-checklist) FORCE_CL=1; shift ;;
    --resend) RESEND=1; shift ;;
    --accept-gate-change) ACCEPT_GATE=(--accept-gate-change); shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done

# ---- resolve ORCA (SKILL.md section 0) and the state file ----
if [ -z "$ORCA" ]; then
  if [ -n "${ORCA_CLI_COMMAND:-}" ]; then ORCA="$ORCA_CLI_COMMAND"
  elif [ -n "${ORCA_DEV_REPO_ROOT:-}" ]; then ORCA="orca-dev"
  elif [ "$(uname -s)" = Linux ] && [ -z "${ORCA_TERMINAL_HANDLE:-}" ] && command -v orca-ide >/dev/null 2>&1; then ORCA="orca-ide"
  else ORCA="orca"; fi
fi
if [ -z "$STATE" ]; then
  [ -n "$SELF" ] || die "--from <self handle> or --state is required outside an Orca terminal"
  STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/$SELF.json"
fi
[ -f "$STATE" ] || die "state file not found: $STATE (run '/set-orchestrator on' first)"
STATE_DIR="$(cd "$(dirname "$STATE")" && pwd)"
[ -n "$SELF" ] || SELF="$(python3 "$ST" field "$STATE" self_handle 2>/dev/null || true)"
[ -n "$PANE" ] || PANE="$(python3 "$ST" field "$STATE" pane_path 2>/dev/null || true)"
[ -n "$PANE" ] && [ -d "$PANE" ] || die "pane path not found: '$PANE' (--pane or state pane_path)"
[ -n "$FAMILY" ] || FAMILY="$(python3 "$ST" field "$STATE" skill_family 2>/dev/null || echo none)"

# ---- validate input ----
[ -n "$TO" ] || die "--to <terminal handle> is required"
[ -n "$TITLE" ] || die "--title is required"
[ -n "$GOAL" ] || die "--goal is required"
[ -n "$CHANGE" ] || die "--change is required"
[ -n "$OWN" ] || die "--ownership is required"
[ ${#ACC[@]} -gt 0 ] || die "at least one --acceptance command is required"
if [ -n "$SPEC_FILE" ]; then [ -f "$SPEC_FILE" ] || die "spec file not found: $SPEC_FILE"; fi
[ -n "$TASK" ] || [ -n "$SPEC_FILE" ] || die "--spec-file is required to create a Task (or pass --task <id>)"
if [ ${#ITEMS[@]} -eq 0 ] && [ -n "$SPEC_FILE" ]; then
  while IFS= read -r line; do [ -n "$line" ] && ITEMS+=("$line"); done < <(python3 "$ST" parse-done-when "$SPEC_FILE")
fi
[ ${#ITEMS[@]} -gt 0 ] || die "Done-when missing: give --item ... or a numbered 'Done-when:' list in the spec"
[ ${#ITEMS[@]} -le 8 ] || die "${#ITEMS[@]} Done-when items (max 8): split into Tasks chained with --deps"
if [ -n "$SPEC_FILE" ] && ! grep -qi 'done[- ]when' "$SPEC_FILE"; then
  die "spec file has no Done-when section; the Task spec must carry the same numbered list as the checklist"
fi
FROM=(); [ "$FROM_GIVEN" = 1 ] && FROM=(--from "$SELF")
[ -n "$AGENT" ] || AGENT="$(python3 -c 'import json,sys
for r in json.load(open(sys.argv[1])).get("roster") or []:
    if r.get("handle")==sys.argv[2]: print(r.get("agent","")); break' "$STATE" "$TO" 2>/dev/null)"
[ -n "$AGENT_TITLE" ] || AGENT_TITLE="$(python3 "$ST" roster-title "$STATE" "$TO" 2>/dev/null || true)"
[ -n "$AGENT_TITLE" ] || AGENT_TITLE="${AGENT:-worker}"

ITEM_ARGS=(); for it in "${ITEMS[@]}"; do ITEM_ARGS+=(--item "$it"); done

if [ "$DRY" = 1 ]; then
  echo "DRY RUN ($ORCA, state $STATE, pane $PANE, ${#ITEMS[@]} items):"
  [ -n "$TASK" ] || echo "  $ORCA orchestration task-create --spec @$SPEC_FILE --task-title \"$TITLE\" ${DEPS:+--deps '$DEPS'} --json"
  echo "  (cd $PANE && python3 $HERE/init-checklist.py --task \${TASK:-<new>} ... ${#ITEMS[@]} items --spec-out $STATE_DIR/specs/<task>.json)"
  echo "  bash $HERE/install-worker-contract.sh --pane $PANE --skill $SKILL --family $FAMILY"
  echo "  $ORCA orchestration dispatch --task <task> --to $TO --json"
  echo "  $ORCA terminal send --terminal $TO --text \"\$(cat brief)\" --enter --wait-submit 15 --json"
  echo "  $ORCA terminal rename --terminal $TO --title \"$AGENT_TITLE · $TITLE\""
  exit 0
fi

orca_json() { # run an orca command, print its stdout; fail on exit != 0, ok:false or state:failed
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
    echo "dispatch.sh: '$ORCA $1 $2' failed (exit $rc): $(cat "$err") $out" >&2
    rm -f "$err"; return 1
  fi
  rm -f "$err"; printf '%s' "$out"
}
now() { python3 "$ST" now; }
upsert() { python3 "$ST" upsert "$STATE" "$1" || echo "dispatch.sh: WARNING could not update $STATE" >&2; }
jstr() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

# ---- 0. freeze the gate scripts (workers can edit the skill dir) ----
GATE_OUT="$(python3 "$ST" gate-snapshot "$STATE" "$HERE" ${ACCEPT_GATE[@]+"${ACCEPT_GATE[@]}"})"; grc=$?
if [ $grc -ne 0 ]; then
  echo "dispatch.sh: gate snapshot refused (exit $grc): $GATE_OUT" >&2; exit 4
fi
GATE_HASH="$(printf '%s' "$GATE_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hash"])')"
GATE_SETTLE="$(printf '%s' "$GATE_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["settle"])')"

# ---- 1. Task ----
if [ -z "$TASK" ]; then
  DEP_ARGS=(); [ -n "$DEPS" ] && DEP_ARGS=(--deps "$DEPS")
  out="$(orca_json orchestration task-create --spec "$(cat "$SPEC_FILE")" --task-title "$TITLE" ${DEP_ARGS[@]+"${DEP_ARGS[@]}"} ${FROM[@]+"${FROM[@]}"} --json)" || exit 1
  TASK="$(printf '%s' "$out" | python3 "$ST" extract-id task)" || { echo "dispatch.sh: no task id in task-create output: $out" >&2; exit 1; }
fi
CHECKLIST="$PANE/.orca/tasks/$TASK/checklist.json"
REPORT="$PANE/.orca/tasks/$TASK/report.json"
SPEC_COPY="$STATE_DIR/specs/$TASK.json"
mkdir -p "$STATE_DIR/specs" "$STATE_DIR/briefs"

# ---- 1b. never send the same brief twice ----
PREV_STATUS="$(python3 "$ST" get "$STATE" "$TASK" status 2>/dev/null || true)"
if [ "$PREV_STATUS" = active ] && [ "$RESEND" = 0 ]; then
  echo "dispatch.sh: $TASK is already active (brief sent); nothing sent. Use --resend to send the brief again." >&2
  exit 3
fi

# ---- 2. checklist + specHash ----
PREV_HASH="$(python3 "$ST" get "$STATE" "$TASK" spec_hash 2>/dev/null || true)"
SPEC_HASH=""
if [ -f "$CHECKLIST" ] && [ "$FORCE_CL" = 0 ]; then
  CUR="$(python3 "$HERE/checklist_spec.py" "$CHECKLIST" 2>/dev/null || true)"
  if [ -n "$PREV_HASH" ] && [ "$CUR" = "$PREV_HASH" ]; then
    SPEC_HASH="$PREV_HASH"; echo "dispatch.sh: reusing checklist $CHECKLIST (specHash matches state)" >&2
  else
    die "checklist exists and does not match the state's spec_hash: $CHECKLIST (new Task id, or --force-checklist)"
  fi
else
  FORCE=(); [ "$FORCE_CL" = 1 ] && FORCE=(--force)
  out="$(cd "$PANE" && python3 "$HERE/init-checklist.py" --task "$TASK" --goal "$GOAL" --worker "$TO" \
        ${PROT[@]+"${PROT[@]}"} "${ACC[@]}" "${ITEM_ARGS[@]}" --spec-out "$SPEC_COPY" ${FORCE[@]+"${FORCE[@]}"} 2>"$STATE_DIR/briefs/$TASK.init.err")" \
    || { echo "dispatch.sh: init-checklist.py failed for $TASK: $(cat "$STATE_DIR/briefs/$TASK.init.err")" >&2; exit 1; }
  rm -f "$STATE_DIR/briefs/$TASK.init.err"
  SPEC_HASH="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["specHash"])')" \
    || { echo "dispatch.sh: init-checklist.py printed no specHash: $out" >&2; exit 1; }
fi

# ---- 3. worker contract (once per pane; no-op when current) ----
bash "$HERE/install-worker-contract.sh" --pane "$PANE" --skill "$SKILL" --family "$FAMILY" --orca "$ORCA" >/dev/null \
  || { echo "dispatch.sh: install-worker-contract.sh failed" >&2; exit 1; }

# ---- 4. dispatch (reuse an existing dispatch_id on re-run) ----
DISPATCH="$(python3 "$ST" get "$STATE" "$TASK" dispatch_id 2>/dev/null || true)"
if [ -z "$DISPATCH" ]; then
  out="$(orca_json orchestration dispatch --task "$TASK" --to "$TO" ${FROM[@]+"${FROM[@]}"} --json)" || {
    upsert "{\"task_id\": $(jstr "$TASK"), \"status\": \"dispatch_failed\", \"spec_hash\": $(jstr "$SPEC_HASH")}"; exit 1; }
  DISPATCH="$(printf '%s' "$out" | python3 "$ST" extract-id dispatch)" || { echo "dispatch.sh: no dispatch id in: $out" >&2; exit 1; }
fi
upsert "$(python3 - "$TASK" "$DISPATCH" "$TO" "$AGENT" "$WAVE" "$ATTEMPT" "$SPEC_HASH" "$CHECKLIST" "$REPORT" "$SPEC_COPY" "$PANE" "$AGENT_TITLE" "$(now)" "$GATE_HASH" <<'PY'
import json, sys
t, d, to, ag, wave, att, h, cl, rep, spec, pane, base, now, gate = sys.argv[1:15]
print(json.dumps({"task_id": t, "dispatch_id": d, "terminal": to, "agent": ag, "wave": int(wave),
                  "attempt": int(att), "status": "dispatched", "started": now, "settled": None,
                  "spec_hash": h, "checklist": cl, "report": rep, "spec_path": spec, "pane": pane,
                  "base_title": base, "gate_hash": gate}))
PY
)"

# ---- 5. short brief + send ----
BRIEF="$STATE_DIR/briefs/$TASK.txt"
FIRST_ACC="$(python3 -c 'import sys; a=sys.argv[1:]; print(a[1] if len(a)>1 else "")' "${ACC[@]}")"
cat > "$BRIEF" <<EOF
Read once if new: .orca/worker-contract.md
Coordinator: $SELF | Task: $TASK | Dispatch: $DISPATCH | You: $TO
Checklist: $CHECKLIST
Report: $REPORT
Skill: $SKILL
Finish ONLY: bash $SKILL/scripts/orca-done.sh --from $TO --task $TASK --dispatch $DISPATCH --checklist $CHECKLIST --report $REPORT --expect-spec-hash $SPEC_HASH --summary "…"
=== TASK ===
Goal: $GOAL
Change: $CHANGE
Ownership: $OWN
Out of scope: $OOS
Acceptance: $FIRST_ACC
Done-when: see checklist (mark with: python3 $SKILL/scripts/mark-item.py --checklist $CHECKLIST --id N --evidence "…" or --run "<cmd>")
EOF
"$ORCA" terminal wait --terminal "$TO" --for tui-idle --timeout-ms 120000 --json >/dev/null 2>&1 || true
if ! orca_json terminal send --terminal "$TO" --text "$(cat "$BRIEF")" --enter --wait-submit 15 --json >/dev/null; then
  upsert "{\"task_id\": $(jstr "$TASK"), \"status\": \"send_failed\"}"; exit 1
fi

# ---- 6. tab title + state ----
TAB="$(python3 -c 'import sys; s=f"{sys.argv[1]} · {sys.argv[2]}"; print(s if len(s)<=40 else s[:39]+"…")' "$AGENT_TITLE" "$TITLE")"
"$ORCA" terminal rename --terminal "$TO" --title "$TAB" --json >/dev/null 2>&1 || echo "dispatch.sh: WARNING rename failed for $TO" >&2
upsert "{\"task_id\": $(jstr "$TASK"), \"status\": \"active\", \"title\": $(jstr "$TAB")}"
python3 -c 'import json,sys; print(json.dumps(dict(zip(["ok","taskId","dispatchId","terminal","specHash","checklist","report","brief","state","gateHash","settle"],[True]+sys.argv[1:]))))' \
  "$TASK" "$DISPATCH" "$TO" "$SPEC_HASH" "$CHECKLIST" "$REPORT" "$BRIEF" "$STATE" "$GATE_HASH" "$GATE_SETTLE"
