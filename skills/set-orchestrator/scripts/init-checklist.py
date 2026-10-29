#!/usr/bin/env python3
"""Create the Done-when checklist for one orchestrated Task.

Usage:
  init-checklist.py --task <id> [--out PATH] --item "step text" [--item ...]
  printf 'step 1\nstep 2\n' | init-checklist.py --task <id>

Optional: --goal "<original goal>", --protected <glob-or-prefix> (repeat),
--acceptance "<command>" (repeat), --worker <terminal handle>, --force,
--no-exclude (do not add .orca/ to .git/info/exclude).

Default output: .orca/tasks/<taskId>/checklist.json relative to the cwd.
Never overwrites an existing checklist unless --force is given.
Works the same for every agent (claude, grok, codex, agy): plain JSON, no hooks.
"""
import argparse, hashlib, json, os, subprocess, sys


def steps_hash(items):
    raw = json.dumps([[i["id"], i["step"]] for i in items], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def git(*args, cwd=None):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Create .orca/tasks/<task>/checklist.json")
    ap.add_argument("--task", required=True)
    ap.add_argument("--out")
    ap.add_argument("--item", action="append", default=[])
    ap.add_argument("--goal", default="")
    ap.add_argument("--protected", action="append", default=[])
    ap.add_argument("--acceptance", action="append", default=[])
    ap.add_argument("--worker", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-exclude", action="store_true")
    a = ap.parse_args()

    items = [s.strip() for s in a.item if s.strip()]
    if not items and not sys.stdin.isatty():
        items = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not items:
        print("init-checklist: no items (use --item or stdin, one per line)", file=sys.stderr)
        return 2
    if len(items) > 8:
        print(f"init-checklist: warning: {len(items)} items; consider splitting into Tasks with --deps", file=sys.stderr)

    out = a.out or os.path.join(".orca", "tasks", a.task, "checklist.json")
    if os.path.exists(out) and not a.force:
        print(f"init-checklist: {out} exists; refusing to overwrite (use --force)", file=sys.stderr)
        return 3
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    entries = [{"id": str(n), "step": s, "passes": False, "evidence": ""} for n, s in enumerate(items, 1)]
    workdir = os.path.dirname(os.path.abspath(out))
    data = {
        "taskId": a.task,
        "version": 1,
        "items": entries,
        "protectedPaths": a.protected,
        "acceptanceCommands": a.acceptance,
        "originalGoal": a.goal,
        "worker": a.worker,
        "baseRef": git("rev-parse", "HEAD", cwd=workdir),
        "stepsHash": steps_hash(entries),
    }
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, out)

    if not a.no_exclude:
        gitdir = git("rev-parse", "--git-common-dir", cwd=workdir)
        if gitdir:
            if not os.path.isabs(gitdir):
                gitdir = os.path.join(workdir, gitdir)
            excl = os.path.join(gitdir, "info", "exclude")
            try:
                os.makedirs(os.path.dirname(excl), exist_ok=True)
                cur = open(excl, encoding="utf-8").read() if os.path.exists(excl) else ""
                if ".orca/" not in cur.split():
                    with open(excl, "a", encoding="utf-8") as f:
                        f.write(("" if cur.endswith("\n") or not cur else "\n") + ".orca/\n")
            except OSError as e:
                print(f"init-checklist: could not update {excl}: {e}", file=sys.stderr)

    print(json.dumps({"ok": True, "checklist": os.path.abspath(out), "taskId": a.task,
                      "items": len(entries), "baseRef": data["baseRef"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
