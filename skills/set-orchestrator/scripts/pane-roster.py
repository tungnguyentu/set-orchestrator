#!/usr/bin/env python3
"""List Orca terminals and mark which are eligible orchestration workers.

Input: `orca terminal list --json` on stdin; when stdin is a tty the resolved
ORCA executable is run instead (ORCA_CLI_COMMAND > orca-dev > orca-ide on Linux
outside Orca > orca).

Usage: pane-roster.py [--pane <worktreePath>] [--self <handle>] [--json] [--title <text>]
  --pane   worktreePath that defines "the same pane" (default: --self's worktreePath)
  --self   the orchestrator's own terminal handle (never eligible)
  --title  resolve one same-pane terminal by its tab title (case-insensitive
           substring, status glyph ignored); prints that row only. Exit 3 when
           no row matches, exit 4 when several match (candidates on stderr).
Eligible = same pane + agentIdentity set + connected + writable + not busy + not self.
"""
import argparse
import json
import os
import subprocess
import sys

BUSY_MARKERS = ("Working", "Thinking", "esc to interrupt")
TITLE_GLYPHS = "✳◑◐◒◓●○◉◎⏺⏸•*· \t"


def norm_title(text):
    return (text or "").lstrip(TITLE_GLYPHS).strip().casefold()


def resolve_orca():
    if os.environ.get("ORCA_CLI_COMMAND"):
        return os.environ["ORCA_CLI_COMMAND"]
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    if sys.platform.startswith("linux") and not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return "orca-ide"
    return "orca"


def load_terminals():
    if sys.stdin.isatty():
        raw = subprocess.run([resolve_orca(), "terminal", "list", "--json"],
                             capture_output=True, text=True, check=True).stdout
    else:
        raw = sys.stdin.read()
    doc = json.loads(raw)
    if not doc.get("ok", True) or "result" not in doc:
        err = doc.get("error") or {}
        sys.exit("orca terminal list failed: %s %s" % (err.get("code", "?"), err.get("message", raw[:200])))
    result = doc["result"]
    return result.get("terminals", result) if isinstance(result, dict) else result


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pane")
    ap.add_argument("--self", dest="self_handle")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--title")
    args = ap.parse_args()

    terminals = load_terminals()
    pane = args.pane
    if not pane and args.self_handle:
        pane = next((t.get("worktreePath") for t in terminals
                     if t.get("handle") == args.self_handle), None)
    if not pane:
        sys.exit("pane-roster: pass --pane <worktreePath> or a --self handle present in the list")

    rows = []
    for t in terminals:
        preview = t.get("preview") or ""
        busy = any(m in preview for m in BUSY_MARKERS)
        is_self = t.get("handle") == args.self_handle
        same_pane = t.get("worktreePath") == pane
        ok_io = bool(t.get("connected")) and bool(t.get("writable"))
        rows.append({
            "handle": t.get("handle"),
            "agent": t.get("agentIdentity") or "",
            "title": t.get("title") or "",
            "branch": (t.get("branch") or "").replace("refs/heads/", ""),
            "worktreePath": t.get("worktreePath"),
            "connected": bool(t.get("connected")),
            "writable": bool(t.get("writable")),
            "busy": busy,
            "self": is_self,
            "samePane": same_pane,
            "eligible": same_pane and bool(t.get("agentIdentity")) and ok_io and not busy and not is_self,
        })

    if args.title:
        want = norm_title(args.title)
        hits = [r for r in rows if r["samePane"] and not r["self"] and want and want in norm_title(r["title"])]
        exact = [r for r in hits if norm_title(r["title"]) == want]
        if len(exact) == 1:
            hits = exact
        if not hits:
            print("pane-roster: no terminal in pane %s has title matching %r" % (pane, args.title), file=sys.stderr)
            sys.exit(3)
        if len(hits) > 1:
            print("pane-roster: %d terminals match title %r:" % (len(hits), args.title), file=sys.stderr)
            for r in hits:
                print("  %s | %s | %s" % (r["handle"], r["agent"] or "-", r["title"]), file=sys.stderr)
            sys.exit(4)
        rows = hits

    if args.json:
        json.dump({"pane": pane, "terminals": rows}, sys.stdout, indent=2)
        print()
        return
    print("handle | agent | title | branch | worktreePath | connected/writable | busy | eligible | self")
    for r in rows:
        print(" | ".join([r["handle"], r["agent"] or "-", str(r["title"]), r["branch"],
                          str(r["worktreePath"]),
                          "%s/%s" % (r["connected"], r["writable"]),
                          "busy" if r["busy"] else "idle",
                          "eligible" if r["eligible"] else "no",
                          "self" if r["self"] else "-"]))


if __name__ == "__main__":
    main()
