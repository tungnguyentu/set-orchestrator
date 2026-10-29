#!/usr/bin/env python3
"""Create the Done-when checklist for one orchestrated Task.

Usage:
  init-checklist.py --task <id> [--out PATH] --item "step text" [--item ...]
  printf 'step 1\nstep 2\n' | init-checklist.py --task <id>

Optional: --goal "<original goal>", --protected <glob-or-prefix> (repeat),
--acceptance "<command>" (repeat), --worker <terminal handle>, --force,
--no-exclude (do not add .orca/ to .git/info/exclude),
--spec-out PATH (coordinator-side copy of the hashed spec; keep it OUTSIDE the worktree,
for example next to the state file: <state dir>/specs/<taskId>.json).

Stdout: one JSON line with "specHash" (sha256 of the canonical spec, see checklist_spec.py).
The coordinator stores that value in its state file (dispatches[].spec_hash) and always
verifies with verify-checklist.py --expect-spec-hash <specHash>; the copy written into
checklist.json is worker-writable and is never trusted on its own.

Default output: .orca/tasks/<taskId>/checklist.json relative to the cwd.
Never overwrites an existing checklist unless --force is given.
Works the same for every agent (claude, grok, codex, agy): plain JSON, no hooks.
"""
import argparse, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checklist_spec import canonical_spec, spec_hash  # noqa: E402


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
    ap.add_argument("--spec-out", help="also write the canonical spec + specHash here (coordinator side)")
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
    }
    data["specHash"] = spec_hash(data)
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

    if a.spec_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.spec_out)), exist_ok=True)
        spec = dict(canonical_spec(data), specHash=data["specHash"], checklist=os.path.abspath(out))
        with open(a.spec_out + ".tmp", "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(a.spec_out + ".tmp", a.spec_out)

    print(f"specHash={data['specHash']}", file=sys.stderr)
    print(json.dumps({"ok": True, "checklist": os.path.abspath(out), "taskId": a.task,
                      "items": len(entries), "baseRef": data["baseRef"], "specHash": data["specHash"],
                      "specOut": os.path.abspath(a.spec_out) if a.spec_out else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
