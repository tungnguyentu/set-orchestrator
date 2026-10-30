#!/usr/bin/env bash
# Print this session's own Orca terminal handle.
#
#   self-handle.sh                      -> $ORCA_TERMINAL_HANDLE (Orca terminal), or
#                                          orca_session_id:$ORCA_AGENT_SESSION_ID (Orca chat tab)
#   self-handle.sh --from <handle>      -> that handle (user-supplied; ignored when the env identifies us)
#   self-handle.sh --nonce              -> prints a one-time marker; step 1 of the probe
#   self-handle.sh --probe <nonce> [--pane <path>] [--orca <exe>] [--json]
#                                       -> step 2: the Orca chat/terminal whose screen shows <nonce>
#
# Orca's chat UI (a "structured" session) has no terminal: it is not in `terminal list` and
# `terminal send/read` cannot reach it. Orca injects ORCA_AGENT_SESSION_ID into its shell; the
# orchestration CLI then acts as that session on its own, and the mailbox address is
# orca_session_id:<id>. A chat can never be named from outside (`--from orca_session_id:...`
# from another shell is refused: session_caller_chat_not_declarable), so only the env proves it.
#
# The nonce probe below is for a Claude TUI that runs in an Orca terminal whose shell lost
# ORCA_TERMINAL_HANDLE: print a nonce in one Bash call (command line and output appear on the
# screen), then in the next call read every Claude terminal of the pane and keep the one that
# shows it.
#
# Exit codes: 0 handle printed; 2 no handle and no probe; 3 probe found no match (candidates on
# stderr); 4 probe matched several sessions; 5 Orca CLI error.
set -u

if [ "${1:-}" != "--probe" ] && [ "${1:-}" != "--nonce" ]; then
  if [ -n "${ORCA_TERMINAL_HANDLE:-}" ]; then
    printf '%s\n' "$ORCA_TERMINAL_HANDLE"
    exit 0
  fi
  if [ -n "${ORCA_AGENT_SESSION_ID:-}" ]; then
    printf 'orca_session_id:%s\n' "$ORCA_AGENT_SESSION_ID"
    echo "self-handle: Orca chat session. Orchestration commands already act as this session; pass this address or nothing as --from/--terminal, never another handle. terminal send/rename/read cannot target it." >&2
    exit 0
  fi
fi

MODE=""; NONCE=""; PANE=""; ORCA_EXE=""; JSON=0; FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="${2:-}"; shift 2 ;;
    --nonce) MODE=nonce; shift ;;
    --probe) MODE=probe; NONCE="${2:-}"; shift 2 ;;
    --pane) PANE="${2:-}"; shift 2 ;;
    --orca) ORCA_EXE="${2:-}"; shift 2 ;;
    --json) JSON=1; shift ;;
    *) echo "self-handle: unknown argument $1" >&2; exit 2 ;;
  esac
done

if [ -n "$FROM" ]; then
  printf '%s\n' "$FROM"
  exit 0
fi

if [ "$MODE" = nonce ]; then
  NONCE="ORCH-SELF-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  printf '%s\n' "$NONCE"
  echo "self-handle: nonce printed. In your NEXT Bash call run:" >&2
  echo "  bash $0 --probe $NONCE --pane \"\$PWD\"" >&2
  exit 0
fi

if [ "$MODE" != probe ] || [ -z "$NONCE" ]; then
  cat >&2 <<'MSG'
self-handle: ORCA_TERMINAL_HANDLE and ORCA_AGENT_SESSION_ID are both empty. An Orca chat tab
always has ORCA_AGENT_SESSION_ID (check `env | grep ORCA_`); without it this shell is outside
Orca, or an Orca terminal whose shell lost its handle. For a Claude TUI in an Orca terminal,
prove the handle with the two-step probe (two separate Bash calls):
  1. bash scripts/self-handle.sh --nonce              # prints ORCH-SELF-...
  2. bash scripts/self-handle.sh --probe ORCH-SELF-... --pane "$PWD"
Or pass a handle the user gave you: --from term_...
MSG
  exit 2
fi

if [ -z "$ORCA_EXE" ]; then
  if [ -n "${ORCA_CLI_COMMAND:-}" ]; then ORCA_EXE="$ORCA_CLI_COMMAND"
  elif [ -n "${ORCA_DEV_REPO_ROOT:-}" ]; then ORCA_EXE=orca-dev
  elif [ "$(uname -s)" = Linux ] && [ -z "${ORCA_TERMINAL_HANDLE:-}" ] && command -v orca-ide >/dev/null 2>&1; then ORCA_EXE=orca-ide
  else ORCA_EXE=orca
  fi
fi
[ -n "$PANE" ] || PANE="$PWD"

LIST="$($ORCA_EXE terminal list --json 2>/dev/null)" || { echo "self-handle: '$ORCA_EXE terminal list' failed" >&2; exit 5; }

# Candidates: Claude sessions in the pane (agentIdentity claude*, connected). Each one's screen is
# read; the one showing the nonce is this session.
CANDS="$(printf '%s' "$LIST" | python3 -I -c '
import json, os, sys
pane = os.path.realpath(sys.argv[1])
d = json.load(sys.stdin)
if not d.get("ok", True):
    print(json.dumps(d.get("error")), file=sys.stderr); sys.exit(5)
for t in d.get("result", {}).get("terminals", []):
    ident = (t.get("agentIdentity") or "")
    if not ident.startswith("claude") or t.get("connected") is False:
        continue
    if os.path.realpath(t.get("worktreePath") or "") != pane:
        continue
    print(t["handle"] + "\t" + (t.get("title") or ""))
' "$PANE")" || exit 5

if [ -z "$CANDS" ]; then
  echo "self-handle: no connected Claude session in pane $PANE (terminal list)" >&2
  exit 3
fi

MATCH=""; N=0
while IFS=$'\t' read -r H T; do
  [ -n "$H" ] || continue
  TAIL="$($ORCA_EXE terminal read --terminal "$H" --limit 200 --json 2>/dev/null | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
print("\n".join(d.get("result", {}).get("terminal", {}).get("tail", []) or []))
' 2>/dev/null)"
  if printf '%s' "$TAIL" | grep -qF -- "$NONCE"; then
    N=$((N + 1)); MATCH="$H"; MATCH_TITLE="$T"
  fi
done <<EOF
$CANDS
EOF

if [ "$N" -eq 1 ]; then
  if [ "$JSON" -eq 1 ]; then
    python3 -I -c 'import json,sys; print(json.dumps({"handle": sys.argv[1], "title": sys.argv[2], "pane": sys.argv[3]}, ensure_ascii=False))' "$MATCH" "$MATCH_TITLE" "$PANE"
  else
    printf '%s\n' "$MATCH"
  fi
  exit 0
fi
if [ "$N" -eq 0 ]; then
  echo "self-handle: nonce $NONCE not on any Claude screen in pane $PANE. Run --nonce again in its own Bash call (the screen must show it), then --probe in the next call. Candidates:" >&2
else
  echo "self-handle: nonce $NONCE shows on $N screens; ask the user which one is this session. Candidates:" >&2
fi
printf '%s\n' "$CANDS" >&2
[ "$N" -eq 0 ] && exit 3 || exit 4
