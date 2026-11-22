#!/usr/bin/env bash
# Make the set-orchestrator skill usable from a Pi session (https://pi.dev/): the Pi session can then
# be the orchestrator with /skill:set-orchestrator on.
#
# Usage: install-pi.sh [--copy] [--uninstall]
# It does two things in ${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}:
#   skills/set-orchestrator      a symbolic link to this skill directory, so Pi always runs the same
#                                scripts as Claude Code (--copy makes a copy; run again after an update)
#   extensions/set-orchestrator.ts  a copy of pi-orchestrator-extension.ts: keeps the role in the system
#                                prompt and looks up the work log for each user prompt (Pi has no hooks)
# An existing skills/set-orchestrator that is not a link made by this script is left alone (exit 3).
# --uninstall removes the link (or the copy this script made) and the extension file.
# Exit: 0 done; 2 bad input; 3 the target exists and is not ours.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
PI_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
COPY=0; UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --copy) COPY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "install-pi.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
DEST="$PI_DIR/skills/set-orchestrator"
EXT="$PI_DIR/extensions/set-orchestrator.ts"
MARK="$DEST/.installed-by-set-orchestrator"

ours() { [ -L "$DEST" ] || [ -f "$MARK" ]; }
drop() { if [ -L "$DEST" ]; then unlink "$DEST"; elif [ -f "$MARK" ]; then find "$DEST" -depth -delete; fi; }

if [ "$UNINSTALL" = 1 ]; then
  if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    ours || { echo "install-pi.sh: $DEST was not installed by this script; left alone" >&2; exit 3; }
    drop
  fi
  [ -f "$EXT" ] && unlink "$EXT"
  echo "install-pi.sh: removed from $PI_DIR"
  exit 0
fi

mkdir -p "$PI_DIR/skills" "$PI_DIR/extensions" || exit 1
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  ours || { echo "install-pi.sh: $DEST exists and was not installed by this script; move it away first" >&2; exit 3; }
  drop
fi
if [ "$COPY" = 1 ]; then
  cp -R "$SKILL" "$DEST" && : > "$MARK" || exit 1
  how="copy"
else
  ln -s "$SKILL" "$DEST" || exit 1
  how="link to $SKILL"
fi
cp "$HERE/pi-orchestrator-extension.ts" "$EXT" || exit 1
echo "install-pi.sh: skill ($how) and extension installed in $PI_DIR. In a Pi session inside an Orca pane: /skill:set-orchestrator on"
