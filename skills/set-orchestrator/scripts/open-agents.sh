#!/usr/bin/env bash
# Open agent terminals in the orchestrator's pane, each started with its
# bypass-permissions flag, and wait until Orca reports the agent identity.
#
# Usage: open-agents.sh --pane <worktreePath> --open <spec> [--next <n>] [--orca <exe>]
#   --open   comma list of <agent>[:<count>][@<model>[/<effort>]], agents: claude, grok, codex, agy
#            e.g. claude:2@sonnet,claude@opus/high,codex. Without @model the agent uses its default.
#   --next   first number to use in the "<agent>-<n>" title (default 1)
# Prints one JSON object per line: {"handle","agent","title","ok","error","model","effort"}.
# Exit 0 when every requested terminal reports an agent identity, 1 otherwise.
set -uo pipefail

ORCA="${ORCA_CLI_COMMAND:-${ORCA_EXE:-orca}}"
PANE=""; SPEC=""; NEXT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --pane) PANE="$2"; shift 2 ;;
    --open) SPEC="$2"; shift 2 ;;
    --next) NEXT="$2"; shift 2 ;;
    --orca) ORCA="$2"; shift 2 ;;
    *) echo "open-agents.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
[ -n "$PANE" ] && [ -n "$SPEC" ] || { echo "open-agents.sh: --pane and --open are required" >&2; exit 2; }

# Launch commands. Claude starts in auto mode (bypass is disabled in the global
# settings); the others keep their bypass flag. CC_LAUNCH keeps the shared
# Serena wrapper when it is installed; CLAUDE_CONFIG_DIR is inherited from the
# orchestrator so the worker uses the same account.
CC_LAUNCH="$HOME/.local/bin/cc-launch"
[ -x "$CC_LAUNCH" ] || CC_LAUNCH="claude"
launch_cmd() {  # <agent> [model] [effort]
  local m="${2:-}" e="${3:-}"
  case "$1" in
    claude) printf '%s%s --permission-mode auto%s%s' "${CLAUDE_CONFIG_DIR:+CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR }" "$CC_LAUNCH" "${m:+ --model $m}" "${e:+ --effort $e}" ;;
    grok) printf 'grok --permission-mode bypassPermissions%s%s' "${m:+ -m $m}" "${e:+ --effort $e}" ;;
    codex) printf 'codex --dangerously-bypass-approvals-and-sandbox%s%s' "${m:+ -m $m}" "${e:+ -c model_reasoning_effort=$e}" ;;
    agy|antigravity) printf 'agy --dangerously-skip-permissions%s%s' "${m:+ --model $m}" "${e:+ --effort $e}" ;;
    *) return 1 ;;
  esac
}
# Model and effort end up inside a shell command line, so only plain id characters pass.
safe_id() { case "$1" in ''|*[!A-Za-z0-9._\[\]-]*) return 1 ;; esac; }
identity_of() {  # agentIdentity Orca reports for the agent id
  case "$1" in agy) echo antigravity ;; *) echo "$1" ;; esac
}

json_field() { python3 -c 'import json,sys; d=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" 2>/dev/null; }

emit() { python3 -c 'import json,sys; a=sys.argv+["",""]; print(json.dumps(dict(zip(["handle","agent","title","ok","error","model","effort"], [a[1], a[2], a[3], a[4]=="1", a[5], a[6], a[7]]))))' "$@"; }

rc=0; n="$NEXT"
IFS=',' read -r -a items <<< "$SPEC"
for item in "${items[@]}"; do
  base="${item%%@*}"; model=""; effort=""
  if [ "$item" != "$base" ]; then
    model="${item#*@}"
    case "$model" in */*) effort="${model#*/}"; model="${model%%/*}" ;; esac
    if ! safe_id "$model" || { [ -n "$effort" ] && ! safe_id "$effort"; }; then
      emit "" "$base" "" 0 "bad model or effort in '$item' (use <agent>[:<count>][@<model>[/<effort>]])"; rc=1; continue
    fi
  fi
  agent="${base%%:*}"; count=1
  [ "$base" != "$agent" ] && count="${base#*:}"
  case "$count" in ''|*[!0-9]*) emit "" "$agent" "" 0 "bad count in '$item'"; rc=1; continue ;; esac
  cmd="$(launch_cmd "$agent" "$model" "$effort")" || { emit "" "$agent" "" 0 "unknown agent id (use claude, grok, codex, agy)"; rc=1; continue; }
  want="$(identity_of "$agent")"
  i=0
  while [ "$i" -lt "$count" ]; do
    i=$((i+1)); title="$want-$n"; n=$((n+1))
    out="$("$ORCA" terminal create --worktree "path:$PANE" --title "$title" --command "$cmd" --json 2>&1)"
    h="$(printf '%s' "$out" | json_field result.handle)"
    [ -n "$h" ] || h="$(printf '%s' "$out" | json_field result.terminal.handle)"
    [ -n "$h" ] || h="$(printf '%s' "$out" | json_field handle)"
    if [ -z "$h" ]; then emit "" "$want" "$title" 0 "terminal create failed: $(printf '%s' "$out" | head -c 300)" "$model" "$effort"; rc=1; continue; fi
    "$ORCA" terminal wait --terminal "$h" --for tui-idle --timeout-ms 90000 --json >/dev/null 2>&1
    got=""
    for _ in 1 2 3 4 5 6; do
      got="$("$ORCA" terminal show --terminal "$h" --json 2>/dev/null | json_field result.terminal.agentIdentity)"
      [ -n "$got" ] && break; sleep 2
    done
    if [ "$got" = "$want" ]; then emit "$h" "$want" "$title" 1 "" "$model" "$effort"
    else emit "$h" "$want" "$title" 0 "agentIdentity is '${got:-none}' after 90 s; the launch command may have failed: $cmd" "$model" "$effort"; rc=1; fi
  done
done
exit "$rc"
