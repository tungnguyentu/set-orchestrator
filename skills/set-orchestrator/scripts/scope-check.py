#!/usr/bin/env python3
"""Scope check of one Task: which committed files lie outside the Task's Ownership.

The gate (settle.sh) checks the checklist, the evidence, protected paths and the acceptance commands.
It does not compare the diff with the Ownership line of the brief. This script does, and it only
reports: it writes nothing and it is not part of the gate. Run it after settle.sh, in dispatch-loop
step 5, and judge every file it names.

Usage:
  scope-check.py --task <task id> [--state FILE] [--json]

What it compares:
  - the commits base..HEAD of the pane, without merge commits, where base is `baseRef` of the Task's
    checklist (the commit the Task started from), made between the Task's `started` time and its
    `settled` time (when the state entry has them). Uncommitted files are not looked at, so the
    user's own uncommitted work never shows up here.
  - the Ownership of the Task (from its brief): paths separated by spaces or commas. A path covers
    itself, everything under it when it is a directory, and anything it matches as a glob.
Workers share one checkout, so base..HEAD can hold commits of other Tasks. A file inside the
Ownership of another Task that was open at some moment between this Task's start and its settlement
is counted as that Task's file and listed under `others`, not under `outside`.

Output (text): one line `scope: N file(s) outside Ownership ...`, then the files with the commit that
touched them. Exit 0 always when the check ran (also with files outside); 2 bad input or no data.
The state file resolves as in dispatch.sh.
"""
import fnmatch, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import worklog  # noqa: E402  (read-only helpers: state resolution, briefs, entries)


def patterns(ownership):
    return [p.strip().rstrip("/") for p in re.split(r"[,\s]+", str(ownership or "")) if p.strip().rstrip("/")]


def covers(pats, path):
    for p in pats:
        if path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p):
            return True
    return False


def git(pane, *args):
    r = subprocess.run(["git", "-C", pane, *args], capture_output=True, timeout=30)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def commits(pane, base, since="", until=""):
    """[(short hash, [files])] of base..HEAD without merges, oldest first, committed in [since, until]."""
    window = ([f"--since={since}"] if since else []) + ([f"--until={until}"] if until else [])
    rc, out = git(pane, "-c", "core.quotepath=off", "log", "--no-merges", "--reverse", "--name-only",
                  "--format=%x01%h", *window, f"{base}..HEAD")
    if rc != 0:
        return None
    res = []
    for block in out.split("\x01")[1:]:
        lines = [l for l in block.splitlines() if l.strip()]
        if lines:
            res.append((lines[0].strip(), lines[1:]))
    return res


def main(argv):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    state = worklog.resolve_state(args)
    task = args[args.index("--task") + 1] if "--task" in args and args.index("--task") + 1 < len(args) else ""
    if not task:
        print("scope-check.py: --task <task id> is required", file=sys.stderr)
        return 2
    if not state or not os.path.isfile(state):
        print(f"scope-check.py: state file not found: '{state}' (pass --state FILE)", file=sys.stderr)
        return 2
    st = worklog.load_json(state)
    pane = str(st.get("pane_path") or "")
    recs = {r["task"]: r for r in worklog.records(state)}
    entries = worklog.state_entries(state)
    me, entry = recs.get(task), entries.get(task) or {}
    if me is None:
        print(f"scope-check.py: no brief for Task {task} in this pane", file=sys.stderr)
        return 2
    own = patterns(me["ownership"])
    base = str(worklog.load_json(me["checklist"]).get("baseRef") or "")
    if not own or not base:
        print(f"scope-check.py: Task {task} has no Ownership or no baseRef; nothing to compare", file=sys.stderr)
        return 2
    started = str(entry.get("started") or "")
    settled = str(entry.get("settled") or "")
    log = commits(pane, base, started, settled)
    if log is None:
        print(f"scope-check.py: git log {base}..HEAD failed in {pane}", file=sys.stderr)
        return 2
    other = []  # Ownership of Tasks that could have committed in the same window
    for t, e in entries.items():
        if t == task or t not in recs:
            continue
        ended = str(e.get("settled") or e.get("abandoned_at") or "")
        if not ended and e.get("status") not in ("dispatched", "active"):
            ended = str(e.get("started") or "")  # closed, time unknown: do not treat it as still open
        if started and ended and ended < started:
            continue  # closed before this Task started
        if settled and str(e.get("started") or "") > settled:
            continue  # started after this Task was settled
        other += [(t, p) for p in patterns(recs[t]["ownership"])]
    outside, others, inside = {}, {}, 0
    for sha, files in log:
        for f in files:
            if covers(own, f):
                inside += 1
            else:
                owner = next((t for t, p in other if covers([p], f)), None)
                (others if owner else outside).setdefault(f, owner or sha)
    result = {"task": task, "base": base, "commits": len(log), "inside": inside,
              "outside": [{"path": p, "commit": c} for p, c in sorted(outside.items())],
              "others": [{"path": p, "task": t} for p, t in sorted(others.items())]}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    n = len(outside)
    print(f"scope: {n} file(s) outside Ownership in {len(log)} commit(s) since {base[:10]}"
          f" ({inside} inside, {len(others)} in another Task's Ownership)")
    for p, c in sorted(outside.items())[:40]:
        print(f"- {p}  (commit {c})")
    if n > 40:
        print(f"- and {n - 40} more; use --json")
    if n:
        print("Judge each file: a test or note next to an owned file is normal, name it in your report; an "
              "unrelated file is scope creep, send a follow-up Task to revert or to justify it. Widen Ownership "
              "in the next spec when the file belonged to the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
