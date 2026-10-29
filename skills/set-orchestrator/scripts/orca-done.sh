#!/usr/bin/env bash
# Shared done-gate for every worker agent (claude, grok, codex, agy).
# The ONLY allowed completion path in orchestrator briefs: it verifies the Task's
# checklist.json and only then sends worker_done. No agent hook is required.
#
# Usage:
#   orca-done.sh --from <workerHandle> --task <id> --dispatch <id> --checklist <path> \
#     [--report <path>] [--subject "..."] [--summary "..."] [--orca <exe>] \
#     [--outcome succeeded|failed] [--skip-gate] [--allow-protected <path>]... \
#     [--cwd <repo dir>] [--expect-spec-hash <specHash>] [--fb-nonce <nonce>] [--dry-run]
#
#   --expect-spec-hash H  passed through to verify-checklist.py (the brief's Finish line carries
#                     the coordinator's specHash, so a checklist whose spec was edited fails here too).
#   --outcome failed  skips the gate and reports honestly which items are not done.
#   --skip-gate       sends without verifying; the body says "gate: SKIPPED" (coordinator treats as unverified).
#   --fb-nonce N      Branch A (no Dispatch): after the gate, print an FB-START ... FB-<N>-END
#                     block on stdout instead of calling orca; --dispatch is then optional.
#   --dry-run         run the gate and print the orca command without sending.
# Exit: 0 sent (or FB block printed); 2 gate failed / bad arguments (nothing sent); other = orca's exit code.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCA="${ORCA_CLI_COMMAND:-${ORCA_EXE:-orca}}"
W=""; TASK=""; DISPATCH=""; CHECKLIST=""; REPORT=""; SUBJECT=""; SUMMARY=""
OUTCOME="succeeded"; SKIP_GATE=0; FB=""; DRY=0; CWD=""; SPEC_HASH=""
ALLOW=()
die() { echo "orca-done.sh: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --from) W="${2:-}"; shift 2 ;;
    --task) TASK="${2:-}"; shift 2 ;;
    --dispatch) DISPATCH="${2:-}"; shift 2 ;;
    --checklist) CHECKLIST="${2:-}"; shift 2 ;;
    --report) REPORT="${2:-}"; shift 2 ;;
    --subject) SUBJECT="${2:-}"; shift 2 ;;
    --summary) SUMMARY="${2:-}"; shift 2 ;;
    --orca) ORCA="${2:-}"; shift 2 ;;
    --outcome) OUTCOME="${2:-}"; shift 2 ;;
    --skip-gate) SKIP_GATE=1; shift ;;
    --allow-protected) ALLOW+=("--allow-protected" "${2:-}"); shift 2 ;;
    --cwd) CWD="${2:-}"; shift 2 ;;
    --expect-spec-hash) SPEC_HASH="${2:-}"; shift 2 ;;
    --fb-nonce) FB="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$TASK" ] || die "--task is required"
[ -n "$CHECKLIST" ] || die "--checklist is required"
[ -f "$CHECKLIST" ] || die "checklist not found: $CHECKLIST (ask the coordinator; never create your own)"
case "$OUTCOME" in succeeded|failed) ;; *) die "--outcome must be succeeded or failed" ;; esac
if [ -z "$FB" ]; then
  [ -n "$W" ] || die "--from <worker handle> is required (your \$ORCA_TERMINAL_HANDLE)"
  [ -n "$DISPATCH" ] || die "--dispatch is required"
fi

GATE="passed"
if [ "$OUTCOME" = "failed" ]; then
  GATE="n/a (outcome failed)"
elif [ "$SKIP_GATE" = 1 ]; then
  GATE="SKIPPED (--skip-gate, unverified)"
else
  args=(--checklist "$CHECKLIST" --protected-diff --rerun-acceptance)
  [ -n "$REPORT" ] && args+=(--require-report "$REPORT")
  [ -n "$CWD" ] && args+=(--cwd "$CWD")
  [ -n "$SPEC_HASH" ] && args+=(--expect-spec-hash "$SPEC_HASH")
  [ ${#ALLOW[@]} -gt 0 ] && args+=("${ALLOW[@]}")
  if ! python3 "$HERE/verify-checklist.py" "${args[@]}"; then
    echo "orca-done.sh: GATE FAILED - worker_done NOT sent. Finish the missing items (mark-item.py with real evidence)" >&2
    echo "  and rerun this command, or rerun with --outcome failed and a --summary that says what is not done." >&2
    exit 2
  fi
fi

BODY="$(python3 - "$CHECKLIST" "$SUMMARY" "$REPORT" "$GATE" <<'PY'
import json, os, sys
path, summary, report, gate = sys.argv[1:5]
d = json.load(open(path, encoding="utf-8"))
lines = ["DONE-WHEN:"]
for it in d.get("items", []):
    ev = str(it.get("evidence", "")).strip()
    if it.get("passes") is True and ev:
        lines.append(f"{it.get('id')}. done: {ev}")
    else:
        lines.append(f"{it.get('id')}. skipped: {ev or 'not done'}")
done = sum(1 for i in d.get("items", []) if i.get("passes") is True and str(i.get("evidence", "")).strip())
lines.append(f"progress: {done}/{len(d.get('items', []))}")
lines.append(f"gate: {gate}")
lines.append(f"checklist: {os.path.abspath(path)}")
if summary:
    lines.append(f"summary: {summary}")
if report:
    lines.append(f"report: {report}")
print("\n".join(lines))
PY
)" || die "cannot read checklist $CHECKLIST"
[ -n "$SUBJECT" ] || SUBJECT="task $TASK: $OUTCOME"

mark_done() {
  python3 - "$CHECKLIST" "$OUTCOME" "$GATE" "${DISPATCH:-}" <<'PY' 2>/dev/null || true
import json, os, sys, time
cl, outcome, gate, disp = sys.argv[1:5]
p = os.path.join(os.path.dirname(os.path.abspath(cl)), "done.json")
json.dump({"outcome": outcome, "gate": gate, "dispatchId": disp, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(p, "w"))
PY
}

if [ -n "$FB" ]; then
  printf 'FB-START\nsubject: %s\noutcome: %s\n%s\n' "$SUBJECT" "$OUTCOME" "$BODY"
  printf 'FB-%s-END\n' "$FB"
  mark_done; exit 0
fi

cmd=("$ORCA" orchestration send --from "$W" --type worker_done --subject "$SUBJECT" --body "$BODY" \
     --task-id "$TASK" --dispatch-id "$DISPATCH" --outcome "$OUTCOME" --json)
if [ "$DRY" = 1 ]; then
  printf 'DRY RUN, not sent:'; printf ' %q' "${cmd[@]}"; printf '\n'; exit 0
fi
"${cmd[@]}"; rc=$?
[ "$rc" = 0 ] && mark_done
exit "$rc"
