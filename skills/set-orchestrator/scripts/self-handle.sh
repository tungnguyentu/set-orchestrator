#!/usr/bin/env bash
# Print this session's own Orca terminal handle.
# Order: $ORCA_TERMINAL_HANDLE -> --from <handle> -> exit 2 with guidance.
# `orca status --json` has no caller handle outside an Orca terminal, so it is not used.
set -u
if [ -n "${ORCA_TERMINAL_HANDLE:-}" ]; then
  printf '%s\n' "$ORCA_TERMINAL_HANDLE"
  exit 0
fi
if [ "${1:-}" = "--from" ] && [ -n "${2:-}" ]; then
  printf '%s\n' "$2"
  exit 0
fi
cat >&2 <<'MSG'
self-handle: ORCA_TERMINAL_HANDLE is empty, so this session is not inside an Orca terminal
and its own handle cannot be proven. Run `ORCA terminal list --json`, pick the terminal in
the current pane that is this session (ask the user if ambiguous), then pass it as:
  --from term_...
MSG
exit 2
