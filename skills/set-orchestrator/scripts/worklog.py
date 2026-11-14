#!/usr/bin/env python3
"""Work log of a pane: what the coordinator already asked for and what came of it.

A coordinator session loses its history at every /compact. The record of its work is still on
disk: one brief per dispatched Task in <state dir>/briefs/<task>.txt (Goal = the user's words,
Change, Ownership), the state entry (title, worker, status, times, verify result) and the
worker's report.json (summary). This script reads them; it writes nothing.

Usage:
  worklog.py recent [-n N] [--state FILE]            the last N Tasks (default 12), newest first
  worklog.py search <word>... [-n N] [--state FILE]  Tasks that contain any of the words, best first
                                                     (default 15): a word found in few Tasks weighs
                                                     more; newest first on ties
  worklog.py show <task id> [--state FILE]           everything known about one Task, with the
                                                     paths of its brief, checklist and report

Search before you ask the user whether something exists or was done, before you write a spec for
an area, and before you forward a worker's question. A match names the Task and its files; read
the report, `git log -- <ownership path>` or the code for the detail. Ask the user only when
those are silent.

Matching ignores case and Vietnamese accents ("chi phí" finds "chi phi" and "CHI PHÍ") and looks
at the title, Goal, Change, Ownership, the Done-when steps (<state dir>/specs/<task>.json), the
report summary and the abandon reason. Hyphens and
underscores separate words.

The state file resolves as in dispatch.sh: --state, else
${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self handle>.json from $ORCA_TERMINAL_HANDLE or
orca_session_id:$ORCA_AGENT_SESSION_ID. Tasks come from every brief in <state dir>/briefs whose
Checklist line lies in this state's pane_path, so earlier Runs of the same pane are included.
Exit: 0 printed (also when nothing matched); 2 bad input or no state file.
"""
import glob, json, os, re, sys, time, unicodedata


def fold(text):
    """Lower case, no accents, words separated by single spaces."""
    t = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def one_line(v, n):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()[:n]


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def state_entries(state_path):
    """{task id: entry} from the state file and the previous_state chain (current file wins)."""
    out, seen, path = {}, set(), state_path
    while path and path not in seen and len(seen) < 20:
        seen.add(path)
        st = load_json(path)
        for d in st.get("dispatches") or []:
            if isinstance(d, dict) and d.get("task_id"):
                out.setdefault(str(d["task_id"]), d)
        prev = st.get("previous_state")
        path = os.path.join(os.path.dirname(state_path), os.path.basename(str(prev))) if prev else None
    return out


BRIEF_KEYS = {"Goal": "goal", "Change": "change", "Ownership": "ownership", "Checklist": "checklist",
              "Report": "report"}


def read_brief(path):
    rec = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                key, sep, val = line.partition(":")
                if sep and key in BRIEF_KEYS and BRIEF_KEYS[key] not in rec:
                    rec[BRIEF_KEYS[key]] = val.strip()
    except OSError:
        pass
    return rec


def records(state_path):
    st = load_json(state_path)
    pane = str(st.get("pane_path") or "").rstrip("/")
    entries = state_entries(state_path)
    recs = []
    for brief in glob.glob(os.path.join(os.path.dirname(state_path), "briefs", "*.txt")):
        task = os.path.basename(brief)[:-4]
        b = read_brief(brief)
        e = entries.get(task) or {}
        where = str(e.get("pane") or "").rstrip("/") or b.get("checklist", "")
        if pane and not (where == pane or where.startswith(pane + "/")):
            continue
        try:
            mtime = os.path.getmtime(brief)
        except OSError:
            mtime = 0
        report = e.get("report") or b.get("report") or ""
        spec = load_json(os.path.join(os.path.dirname(state_path), "specs", task + ".json"))
        steps = [str(i.get("step") or "") for i in spec.get("items") or [] if isinstance(i, dict)]
        verify = e.get("verify") if isinstance(e.get("verify"), dict) else {}
        recs.append({
            "task": task, "when": str(e.get("started") or time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(mtime))),
            "sort": mtime, "title": e.get("title") or "", "worker": e.get("base_title") or e.get("terminal") or "",
            "status": e.get("status") or "not in the state file", "goal": b.get("goal", ""),
            "change": b.get("change", ""), "ownership": b.get("ownership", ""), "steps": steps,
            "summary": load_json(report).get("summary") or "", "abandon_reason": e.get("abandon_reason") or "",
            "verify_ok": verify.get("ok"), "brief": brief, "checklist": e.get("checklist") or b.get("checklist", ""),
            "report": report,
        })
    recs.sort(key=lambda r: r["sort"], reverse=True)
    return recs


def outcome(r):
    s = r["status"]
    if s == "abandoned":
        return "abandoned (gate not run)" + (f": {one_line(r['abandon_reason'], 80)}" if r["abandon_reason"] else "")
    if s == "settled":
        return "settled, gate passed" if r["verify_ok"] else "settled"
    return s


def line(r):
    out = [f"- {one_line(r['when'], 16)} {r['task']} [{outcome(r)}] {one_line(r['title'], 60)}"]
    if r["change"]:
        out.append(f"    change: {one_line(r['change'], 200)}")
    if r["goal"]:
        out.append(f"    user asked: {one_line(r['goal'], 200)}")
    if r["summary"]:
        out.append(f"    result: {one_line(r['summary'], 200)}")
    return "\n".join(out)


def resolve_state(argv):
    if "--state" in argv:
        i = argv.index("--state")
        path = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
        return path
    me = os.environ.get("ORCA_TERMINAL_HANDLE") or ""
    if not me and os.environ.get("ORCA_AGENT_SESSION_ID"):
        me = "orca_session_id:" + os.environ["ORCA_AGENT_SESSION_ID"]
    if not me:
        return ""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "orchestrator", me.replace(":", "_") + ".json")


def take_n(argv, default):
    if "-n" not in argv:
        return default
    i = argv.index("-n")
    val = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i:i + 2]
    if not val.isdigit() or int(val) < 1:
        raise ValueError(f"-n must be a positive integer (got '{val}')")
    return int(val)


def main(argv):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    cmd = args.pop(0)
    state = resolve_state(args)
    if not state or not os.path.isfile(state):
        print(f"worklog.py: state file not found: '{state}' (pass --state FILE)", file=sys.stderr)
        return 2
    try:
        n = take_n(args, 12 if cmd == "recent" else 15)
    except ValueError as e:
        print(f"worklog.py: {e}", file=sys.stderr)
        return 2
    recs = records(state)
    if cmd == "recent":
        if args:
            print("worklog.py: recent takes no words", file=sys.stderr)
            return 2
        print(f"{min(n, len(recs))} of {len(recs)} Tasks of this pane, newest first:")
        for r in recs[:n]:
            print(line(r))
        return 0
    if cmd == "search":
        words = sorted(set(fold(" ".join(args)).split()))
        if not words:
            print("worklog.py: search needs at least one word", file=sys.stderr)
            return 2
        hays = [set(fold(" ".join((r["title"], r["goal"], r["change"], r["ownership"], r["summary"],
                                   r["abandon_reason"], *r["steps"]))).split()) for r in recs]
        df = {w: sum(1 for h in hays if w in h) for w in words}  # a word found in few Tasks weighs more
        scored = []
        for r, hay in zip(recs, hays):
            found = [w for w in words if w in hay]
            if found:
                scored.append((round(sum(1.0 / df[w] for w in found), 6), r, len(found)))
        scored.sort(key=lambda x: (x[0], x[1]["sort"]), reverse=True)
        print(f"{len(scored)} of {len(recs)} Tasks match {' '.join(words)}"
              + (f"; the {n} best:" if len(scored) > n else ":"))
        for _, r, hits in scored[:n]:
            print(line(r) + f"\n    matched {hits} of {len(words)} words; detail: worklog.py show {r['task']}")
        if not scored:
            print("Nothing in the work log. Check `git log` and the code before you ask the user.")
        return 0
    if cmd == "show":
        if len(args) != 1:
            print("worklog.py: show needs one Task id", file=sys.stderr)
            return 2
        r = next((x for x in recs if x["task"] == args[0]), None)
        if r is None:
            print(f"worklog.py: no Task {args[0]} in this pane's work log", file=sys.stderr)
            return 2
        print(line(r))
        for n, step in enumerate(r["steps"], 1):
            print(f"    done-when {n}: {one_line(step, 300)}")
        for k in ("worker", "ownership", "brief", "checklist", "report"):
            if r[k]:
                print(f"    {k}: {one_line(r[k], 400)}")
        return 0
    print(f"worklog.py: unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
