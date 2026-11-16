#!/usr/bin/env bash
# Let Claude invoke the pstack plugin's skills by itself: remove the line
# "disable-model-invocation: true" from every SKILL.md of the installed pstack plugin.
# A plugin update restores the original files, so run this again after
#   claude plugin update pstack@cursor-plugins
# OPTIONAL, not used by the orchestrator: workers can already call the pstack skills that their brief names.
# Use it when you want Claude to pick pstack skills by itself in ordinary sessions too.
# Usage: pstack-allow-model.sh [config dir ...]   (default: $CLAUDE_CONFIG_DIR and ~/.claude)
# Undo: reinstall the plugin (claude plugin uninstall pstack@cursor-plugins, then install it again).
set -u
dirs=("$@"); [ ${#dirs[@]} -gt 0 ] || dirs=("${CLAUDE_CONFIG_DIR:-}" "$HOME/.claude")
changed=0; seen=0
for d in "${dirs[@]}"; do
  [ -n "$d" ] && [ -d "$d/plugins/cache" ] || continue
  for f in "$d"/plugins/cache/*/pstack/*/skills/*/SKILL.md; do
    [ -f "$f" ] || continue
    seen=$((seen + 1))
    if grep -q '^disable-model-invocation: true[[:space:]]*$' "$f"; then
      python3 - "$f" <<'PY' && changed=$((changed + 1))
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(re.sub(r"(?m)^disable-model-invocation: true[ \t]*\n", "", s, count=1))
PY
    fi
  done
done
echo "pstack-allow-model.sh: $seen skill files seen, $changed changed"
