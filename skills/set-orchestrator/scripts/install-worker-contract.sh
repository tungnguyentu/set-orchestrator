#!/usr/bin/env bash
# Install the shared worker contract into a pane: <pane>/.orca/worker-contract.md
# Workers read it once; per-dispatch briefs stay short and point to it.
#
# Usage:
#   install-worker-contract.sh --pane <pane path> --skill <abs skill dir> \
#     --family ak|ce|superpowers|none [--orca <exe>] [--force]
#
# Method/Context lines come from the "Method and Context lines per skill family" table in
# references/coordinator-loop.md (single source of truth).
# Idempotent: when the file exists, only the marked skill/Method/Context blocks are rewritten
# (the rest stays byte-stable) and nothing is written if they are unchanged. --force rewrites
# the whole file from references/worker-contract.md.
# Also adds `.orca/` to the repo's .git/info/exclude (local only).
# Exit: 0 installed/updated/unchanged; 2 bad arguments or unparseable files.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PANE=""; SKILL=""; FAMILY=""; FORCE=0; ORCA_EXE="${ORCA_CLI_COMMAND:-orca}"
die() { echo "install-worker-contract.sh: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --pane) PANE="${2:-}"; shift 2 ;;
    --skill) SKILL="${2:-}"; shift 2 ;;
    --family) FAMILY="${2:-}"; shift 2 ;;
    --orca) ORCA_EXE="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$PANE" ] || die "--pane is required"
[ -d "$PANE" ] || die "pane not found: $PANE"
[ -n "$SKILL" ] || SKILL="$(cd "$HERE/.." && pwd)"
case "$SKILL" in /*) ;; *) die "--skill must be an absolute path" ;; esac
[ -f "$SKILL/scripts/orca-done.sh" ] || die "not a set-orchestrator skill dir (no scripts/orca-done.sh): $SKILL"
case "$FAMILY" in ak|ce|superpowers|none) ;; *) die "--family must be ak|ce|superpowers|none" ;; esac

TEMPLATE="$SKILL/references/worker-contract.md"
TABLE="$SKILL/references/coordinator-loop.md"
[ -f "$TEMPLATE" ] || die "template not found: $TEMPLATE"
[ -f "$TABLE" ] || die "table not found: $TABLE"
DEST_DIR="$PANE/.orca"
DEST="$DEST_DIR/worker-contract.md"
mkdir -p "$DEST_DIR"

python3 - "$TEMPLATE" "$TABLE" "$DEST" "$SKILL" "$FAMILY" "$ORCA_EXE" "$FORCE" <<'PY'
import os, re, sys
template, table, dest, skill, family, orca, force = sys.argv[1:8]
force = force == "1"
TAIL = "If a named skill is not installed in this terminal, say so in your worker_done summary and work without it."

rows = {}
in_sec = False
for line in open(table, encoding="utf-8"):
    if line.startswith("## "):
        in_sec = line.strip().lower().startswith("## method and context lines per skill family")
        continue
    if not in_sec or not line.startswith("| `"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 3:
        continue
    m = re.match(r"`([a-z]+)`", cells[0])
    if m:
        rows[m.group(1)] = (cells[1], cells[2])
if family not in rows:
    sys.exit(f"install-worker-contract.sh: family {family!r} not found in table of {table}")
method, context = rows[family]
ref = re.fullmatch(r"Same as `([a-z]+)`\.?", context)
if ref and ref.group(1) in rows:
    context = rows[ref.group(1)][1]
method_block = f"Skill family: `{family}`. {method} {TAIL}"
context_block = context
skill_block = None  # taken from rendered template

tpl = open(template, encoding="utf-8").read()
rendered = (tpl.replace("{{SKILL_DIR}}", skill).replace("{{ORCA}}", orca)
               .replace("{{METHOD}}", method_block).replace("{{CONTEXT}}", context_block))

def block(text, name):
    m = re.search(rf"(<!-- worker-contract:{name}:start -->\n)(.*?)(\n?<!-- worker-contract:{name}:end -->)", text, re.S)
    return m

names = ("skill", "method", "context")
if force or not os.path.exists(dest):
    new, status = rendered, ("rewritten (--force)" if os.path.exists(dest) else "installed")
else:
    cur = open(dest, encoding="utf-8").read()
    if "<!-- worker-contract:v1 -->" not in cur or not all(block(cur, n) for n in names):
        sys.exit(f"install-worker-contract.sh: {dest} exists without contract markers; rerun with --force")
    new = cur
    for n in names:
        want = block(rendered, n).group(2)
        m = block(new, n)
        new = new[:m.start(2)] + want + new[m.end(2):]
    status = "unchanged" if new == cur else "updated (skill/Method/Context blocks)"
if status != "unchanged":
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, dest)
import json
print(json.dumps({"ok": True, "path": dest, "status": status, "family": family, "skill": skill}))
PY

# Keep .orca/ out of commits (local exclude only).
if EXC="$(git -C "$PANE" rev-parse --git-path info/exclude 2>/dev/null)"; then
  case "$EXC" in /*) ;; *) EXC="$PANE/$EXC" ;; esac
  mkdir -p "$(dirname "$EXC")"
  grep -qxF '.orca/' "$EXC" 2>/dev/null || echo '.orca/' >> "$EXC"
fi
