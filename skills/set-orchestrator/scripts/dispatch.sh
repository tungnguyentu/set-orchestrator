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
#     [--family ak|ce|superpowers|none] [--orca <exe>] [--force-checklist] [--resend] [--accept-gate-change] [--dry-run] \
#     [--tier light|standard|heavy|frontier] [--tier-override "<reason>"] [--frontier-accepted "<user's words>"] \
#     [--max-per-context N] [--keep-context "<reason>"]
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
# Tier guard (before anything is created): --tier is the Task's tier, chosen by the coordinator
# (references/model-routing.md). The worker's tier comes from its roster[] model in the state file
# (orch_state.py model-tier). A different tier exits 5 and lists the roster workers of the Task's
# tier; --tier-override "<reason>" lets it through and stores the reason as tier_override. Without
# --tier the dispatch proceeds with a warning. The entry records tier, model and effort.
# Frontier guard: a Task is frontier when --tier frontier is given or the worker's model maps to
# frontier (fable). It needs the user's acceptance for that Task, either a prior
# "orch_state.py accept-frontier STATE TASK --quote ..." record (with --task) or
# --frontier-accepted "<user's words>", stored once the Task id is known. Neither: exit 6.
# --tier-override never bypasses it. A fable worker given a non-frontier Task needs only the
# acceptance (no override). The entry records frontier_quote.
# Open-dispatch guard (before anything is created, after the input checks): one open Dispatch per
# terminal. Any other Task of the state file with this --to terminal and status dispatched or active
# exits 7, naming the worker and each open Task (id, title, start time). Settle each with the frozen
# settle.sh, or mark it with "orch_state.py abandon". There is no override flag.
# Context guard (next): a Claude worker (agent from the roster entry, else --agent; its name starts
# with "claude") that already took the limit's number of Tasks since its last context reset
# (orch_state.py tasks-since-reset; context_resets in the state file) exits 8 and the message gives the
# fresh-worker.sh command to run. The limit is 3, set with --max-per-context N or the env
# ORCH_MAX_TASKS_PER_CONTEXT (the flag wins; a positive integer). --keep-context "<reason>" lets the
# Task through (it continues the previous one) and stores keep_context. Other agents (codex, grok,
# antigravity) are not checked. A --resend of a Task already in the state is not blocked by, nor
# counted with, its own entry.
# Exit: 0 dispatched and sent; 4 gate scripts changed (nothing created); 2 bad input; 3 already active (nothing sent); 1 an Orca step failed;
# 5 Task tier differs from the worker's tier (nothing created); 6 frontier dispatch without the user's acceptance (nothing created);
# 7 the terminal has another open Dispatch (nothing created); 8 the Claude worker's context is full (nothing created).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
ST="$HERE/orch_state.py"
TO=""; TITLE=""; SPEC_FILE=""; GOAL=""; CHANGE=""; OWN=""; OOS="see Task spec"; TASK=""; DEPS=""
SELF="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"; FROM_GIVEN=0; STATE=""; PANE=""; AGENT=""; AGENT_TITLE=""
TIER=""; TIER_OVERRIDE=""; FRONTIER_QUOTE=""; OVERRIDE_GIVEN=0; QUOTE_GIVEN=0
MAX_CTX=""; MAX_GIVEN=0; KEEP_CONTEXT=""; KEEP_GIVEN=0
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
    --tier) TIER="${2:-}"; shift 2 ;;
    --tier-override) TIER_OVERRIDE="${2:-}"; OVERRIDE_GIVEN=1; shift 2 ;;
    --frontier-accepted) FRONTIER_QUOTE="${2:-}"; QUOTE_GIVEN=1; shift 2 ;;
    --max-per-context) MAX_CTX="${2:-}"; MAX_GIVEN=1; shift 2 ;;
    --keep-context) KEEP_CONTEXT="${2:-}"; KEEP_GIVEN=1; shift 2 ;;
    -h|--help) sed -n '2,55p' "$0"; exit 0 ;;
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
  STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${SELF//:/_}.json"  # chat addresses contain ":"
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

# ---- guards (nothing is created before they pass): input, open dispatch, context, tier, frontier ----
case "$TIER" in ""|light|standard|heavy|frontier) ;; *) die "--tier must be light, standard, heavy or frontier (got '$TIER')" ;; esac
[ "$OVERRIDE_GIVEN" = 0 ] || [ -n "$TIER_OVERRIDE" ] || die "--tier-override needs a non-empty reason"
[ "$QUOTE_GIVEN" = 0 ] || [ -n "$FRONTIER_QUOTE" ] || die "--frontier-accepted needs the user's words (non-empty)"
[ "$KEEP_GIVEN" = 0 ] || [ -n "$KEEP_CONTEXT" ] || die "--keep-context needs a reason (non-empty)"
if [ "$MAX_GIVEN" = 0 ]; then MAX_CTX="${ORCH_MAX_TASKS_PER_CONTEXT:-3}"; fi
case "$MAX_CTX" in ''|*[!0-9]*) die "--max-per-context / ORCH_MAX_TASKS_PER_CONTEXT must be a positive integer (got '$MAX_CTX')" ;; esac
[ "$MAX_CTX" -ge 1 ] || die "--max-per-context / ORCH_MAX_TASKS_PER_CONTEXT must be a positive integer (got '$MAX_CTX')"
EXCLUDE=(); [ -z "$TASK" ] || EXCLUDE=(--exclude "$TASK")  # a Task already in the state is not blocked or counted by its own entry

OPEN="$(python3 "$ST" open-dispatches "$STATE" "$TO" ${EXCLUDE[@]+"${EXCLUDE[@]}"})"
if [ -n "$OPEN" ]; then
  echo "dispatch.sh: worker $AGENT_TITLE ($TO) still has open dispatches; nothing created. Open Tasks:" >&2
  printf '%s\n' "$OPEN" | sed 's/^/  /' >&2
  echo "Settle each with the frozen settle.sh (<state dir>/gate/<gate_hash>/settle.sh --from $SELF --task <id>), or mark it with: python3 $ST abandon $STATE --task <id> --reason \"<why>\"" >&2
  exit 7
fi

W_AGENT="$(python3 "$ST" worker-info "$STATE" "$TO" agent 2>/dev/null || true)"; [ -n "$W_AGENT" ] || W_AGENT="$AGENT"
case "$W_AGENT" in
  claude*)
    CTX_N="$(python3 "$ST" tasks-since-reset "$STATE" "$TO" ${EXCLUDE[@]+"${EXCLUDE[@]}"})"
    if [ "${CTX_N:-0}" -ge "$MAX_CTX" ]; then
      if [ -n "$KEEP_CONTEXT" ]; then
        echo "dispatch.sh: keeping the context of $AGENT_TITLE ($CTX_N Tasks since its last reset, limit $MAX_CTX): $KEEP_CONTEXT" >&2
      else
        echo "dispatch.sh: worker $AGENT_TITLE ($TO) already took $CTX_N Tasks since its last context reset (limit $MAX_CTX); nothing created. Give it a fresh context first: bash $HERE/fresh-worker.sh --terminal $TO --state $STATE   (or pass --keep-context \"<reason>\" when this Task continues the previous one)." >&2
        exit 8
      fi
    fi ;;
esac

W_MODEL="$(python3 "$ST" worker-info "$STATE" "$TO" model 2>/dev/null || true)"
W_EFFORT="$(python3 "$ST" worker-info "$STATE" "$TO" effort 2>/dev/null || true)"
W_TIER="$(python3 "$ST" model-tier "$W_MODEL")"
ACCEPTED_QUOTE="$FRONTIER_QUOTE"
if [ -z "$ACCEPTED_QUOTE" ] && [ -n "$TASK" ]; then ACCEPTED_QUOTE="$(python3 "$ST" frontier-accepted "$STATE" "$TASK" quote 2>/dev/null || true)"; fi
IS_FRONTIER=0; { [ "$TIER" = frontier ] || [ "$W_TIER" = frontier ]; } && IS_FRONTIER=1
[ "$IS_FRONTIER" = 1 ] || ACCEPTED_QUOTE=""
if [ "$IS_FRONTIER" = 1 ]; then
  if [ -z "$ACCEPTED_QUOTE" ]; then
    echo "dispatch.sh: frontier dispatch (Task tier '${TIER:-none}', worker $AGENT_TITLE on '${W_MODEL:-unknown}' = $W_TIER) needs the user's yes for this Task; nothing created. Ask the user, then pass --frontier-accepted \"<their words>\" (or run: orch_state.py accept-frontier $STATE <task id> --quote \"<their words>\" and pass --task). --tier-override does not bypass this." >&2
    exit 6
  fi
fi
if [ -z "$TIER" ]; then
  echo "dispatch.sh: WARNING no --tier given; the Task's tier was not checked against the worker (${AGENT_TITLE} on '${W_MODEL:-unknown}' = $W_TIER)." >&2
elif [ "$TIER" != "$W_TIER" ] && [ "$W_TIER" != frontier ]; then
  if [ -n "$TIER_OVERRIDE" ]; then
    echo "dispatch.sh: tier override accepted ($TIER Task on $W_TIER worker): $TIER_OVERRIDE" >&2
  else
    MATCHES="$(python3 "$ST" tier-workers "$STATE" "$TIER" | paste -sd ',' - | sed 's/,/, /g')"
    echo "dispatch.sh: tier mismatch: Task tier '$TIER' but worker $AGENT_TITLE runs '${W_MODEL:-unknown}' (tier $W_TIER); nothing created. Roster workers of tier $TIER: ${MATCHES:-none}. Send it to one of them, switch a worker's model, or pass --tier-override \"<reason>\"." >&2
    exit 5
  fi
else
  TIER_OVERRIDE=""  # nothing to override: do not record a reason for a matching dispatch
fi

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
if [ -n "$FRONTIER_QUOTE" ]; then
  python3 "$ST" accept-frontier "$STATE" "$TASK" --quote "$FRONTIER_QUOTE" || { echo "dispatch.sh: could not record the frontier acceptance for $TASK" >&2; exit 1; }
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
upsert "$(python3 - "$TASK" "$DISPATCH" "$TO" "$AGENT" "$WAVE" "$ATTEMPT" "$SPEC_HASH" "$CHECKLIST" "$REPORT" "$SPEC_COPY" "$PANE" "$AGENT_TITLE" "$(now)" "$GATE_HASH" "$TIER" "$W_MODEL" "$W_EFFORT" "$TIER_OVERRIDE" "$ACCEPTED_QUOTE" "$KEEP_CONTEXT" <<'PY'
import json, sys
t, d, to, ag, wave, att, h, cl, rep, spec, pane, base, now, gate, tier, model, effort, override, quote, keep = sys.argv[1:21]
entry = {"task_id": t, "dispatch_id": d, "terminal": to, "agent": ag, "wave": int(wave),
         "attempt": int(att), "status": "dispatched", "started": now, "settled": None,
         "spec_hash": h, "checklist": cl, "report": rep, "spec_path": spec, "pane": pane,
         "base_title": base, "gate_hash": gate}
# optional keys are written only when known, so a --resend without them keeps the earlier values
for key, val in (("tier", tier), ("model", model), ("effort", effort), ("tier_override", override),
                 ("frontier_quote", quote), ("keep_context", keep)):
    if val:
        entry[key] = val
print(json.dumps(entry))
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
