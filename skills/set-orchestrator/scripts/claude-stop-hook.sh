#!/usr/bin/env bash
# OPTIONAL, Claude Code workers only. Grok, Codex and agy have no equivalent;
# for them orca-done.sh + coordinator verify is the whole gate.
#
# Stop hook: blocks Claude from ending its turn while a checklist assigned to
# this terminal ($ORCA_TERMINAL_HANDLE == checklist.worker) is unfinished and
# has no done.json (written by orca-done.sh after worker_done or --outcome failed).
# Exit 2 + stderr = Claude keeps working and reads the reason. Never blocks twice
# in a row (stop_hook_active) to avoid loops. No checklist, no handle: exit 0.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat 2>/dev/null || true)"
ACTIVE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin).get("stop_hook_active", False)).lower())
except Exception: print("false")' 2>/dev/null)"
[ "$ACTIVE" = "true" ] && exit 0
ME="${ORCA_TERMINAL_HANDLE:-}"
[ -n "$ME" ] || exit 0
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
for cl in "$ROOT"/.orca/tasks/*/checklist.json; do
  [ -f "$cl" ] || continue
  [ -f "$(dirname "$cl")/done.json" ] && continue
  owner="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("worker",""))' "$cl" 2>/dev/null)"
  [ "$owner" = "$ME" ] || continue
  if ! python3 "$HERE/verify-checklist.py" --checklist "$cl" >/dev/null 2>/tmp/orca-stop-hook.$$; then
    {
      echo "Orca checklist not finished: $cl"
      cat /tmp/orca-stop-hook.$$
      echo "Finish each item and record evidence with mark-item.py, then complete with orca-done.sh."
      echo "If you cannot finish, run orca-done.sh ... --outcome failed --summary \"<what is not done>\"."
    } >&2
    rm -f /tmp/orca-stop-hook.$$
    exit 2
  fi
  rm -f /tmp/orca-stop-hook.$$
  echo "Orca checklist $cl passes but worker_done was not sent: run orca-done.sh now." >&2
  exit 2
done
exit 0
