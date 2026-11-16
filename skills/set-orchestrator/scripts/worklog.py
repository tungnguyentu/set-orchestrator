#!/usr/bin/env python3
"""Work log of a pane: what the coordinator already asked for and what came of it.

A coordinator session loses its history at every /compact. The record of its work is still on
disk: one brief per dispatched Task in <state dir>/briefs/<task>.txt (Goal = the user's words,
Change, Ownership), the state entry (title, worker, status, times, verify result) and the
worker's report.json (summary). This script reads them; it writes nothing.

Usage:
  worklog.py recent [-n N] [--state FILE]            the last N Tasks (default 12), newest first
  worklog.py search <word>... [-n N] [--brief] [--state FILE]
                                                     Tasks that contain any of the words, best first
                                                     (default 15), then the commits of the pane that
                                                     match, then the Tasks and commits that removed or
                                                     hid something and match. A word found in few
                                                     entries weighs more, a word found in more than a
                                                     quarter of them is ignored, two words in the same
                                                     order as in the query add to the score; newest
                                                     first on ties. --brief prints one line per Task.
  worklog.py show <task id> [--state FILE]           everything known about one Task, with the
                                                     paths of its brief, checklist and report

Search before you ask the user whether something exists or was done, before you write a spec for
an area, and before you forward a worker's question. A match names the Task and its files; read
the report for the detail. This script knows Tasks, not code: search the code with the GitNexus or
Serena tools when the session has them, else `git log -- <ownership path>` and grep. Ask the user
only when those are silent.

Matching ignores case and Vietnamese accents ("chi phí" finds "chi phi" and "CHI PHÍ") and looks
at the title, Goal, Change, Ownership, the Done-when steps (<state dir>/specs/<task>.json), the
report summary and the abandon reason. Hyphens and
underscores separate words. Commits are the subjects of `git log` in the pane (the last 4000). An
entry "removed or hid something" when its title, Change, Goal or subject has one of the words in
REMOVAL (bỏ, xóa, gỡ, ẩn, tắt, remove, drop, delete, hide, disable).

The state file resolves as in dispatch.sh: --state, else
${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self handle>.json from $ORCA_TERMINAL_HANDLE or
orca_session_id:$ORCA_AGENT_SESSION_ID. Tasks come from every brief in <state dir>/briefs whose
Checklist line lies in this state's pane_path, so earlier Runs of the same pane are included.
Exit: 0 printed (also when nothing matched); 2 bad input or no state file.
"""
import glob, json, math, os, re, subprocess, sys, time, unicodedata


def fold(text):
    """Lower case, no accents, words separated by single spaces."""
    t = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


REMOVAL = {unicodedata.normalize("NFC", w) for w in (
    "bỏ", "xóa", "xoá", "gỡ", "ẩn", "tắt", "remove", "removed", "removes", "drop", "dropped", "delete", "deleted",
    "hide", "hidden", "disable", "disabled")}


def says_removed(text):
    t = unicodedata.normalize("NFC", str(text or "")).lower()
    return any(w in REMOVAL for w in re.findall(r"\w+", t))


def commits(pane, limit=4000):
    """Subjects of the pane's commits, newest first; [] when the pane is not a git checkout."""
    if not pane or not os.path.isdir(pane):
        return []
    try:
        out = subprocess.run(["git", "-C", pane, "log", "-n", str(limit), "--format=%h%x09%ad%x09%s", "--date=short"],
                             capture_output=True, timeout=8).stdout.decode("utf-8", "replace")
    except Exception:
        return []
    rows = [l.split("\t", 2) for l in out.splitlines()]
    return [{"hash": r[0], "date": r[1], "subject": r[2]} for r in rows if len(r) == 3]


def rank(query, texts):
    """[(score, index, words found)] best first for the folded query (a list of words, in order).
    texts keep their own order on ties, so pass them newest first."""
    words = sorted(set(query))
    hays = [fold(t) for t in texts]
    sets = [set(h.split()) for h in hays]
    n = len(sets)
    df = {w: sum(1 for s in sets if w in s) for w in words}
    use = [w for w in words if df[w] and (n < 20 or df[w] <= n / 4)] or [w for w in words if df[w]]
    pairs = {f" {a} {b} " for a, b in zip(query, query[1:]) if a != b}
    out = []
    for i, (hay, s) in enumerate(zip(hays, sets)):
        score = sum(math.log(1 + n / df[w]) for w in use if w in s)
        if score:
            padded = f" {hay} "
            score += 1.5 * sum(1 for p in pairs if p in padded)
            out.append((round(score, 6), i, sum(1 for w in words if w in s)))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


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


def brief_line(r):
    return (f"- {one_line(r['when'], 10)} {r['task']} [{one_line(outcome(r), 40)}] {one_line(r['title'], 50)}"
            + (f": {one_line(r['change'], 120)}" if r["change"] else ""))


def commit_line(c):
    return f"- {c['hash']} {c['date']} {one_line(c['subject'], 150)}"


def task_text(r):
    return " ".join((r["title"], r["goal"], r["change"], r["ownership"], r["summary"], r["abandon_reason"], *r["steps"]))


def search(recs, pane, text, n, brief):
    """Print the Tasks, commits and removals that match; return how many entries were printed."""
    query = fold(text).split()
    words = sorted(set(query))
    shown = " ".join(words[:12]) + (f" (+{len(words) - 12} more words)" if len(words) > 12 else "")
    scored = rank(query, [task_text(r) for r in recs])
    print(f"{len(scored)} of {len(recs)} Tasks match {shown}" + (f"; the {n} best:" if len(scored) > n else ":"))
    for _, i, hits in scored[:n]:
        r = recs[i]
        print(brief_line(r) if brief else
              line(r) + f"\n    matched {hits} of {len(words)} words; detail: worklog.py show {r['task']}")
    if not scored:
        print("Nothing in the work log. Search the code (GitNexus or Serena when available, else `git log` "
              "and grep) before you ask the user.")
    log = commits(pane)
    found = rank(query, [c["subject"] for c in log])
    if found:
        print(f"{len(found)} of {len(log)} commits match" + (f"; the {n} best:" if len(found) > n else ":"))
        for _, i, _ in found[:n]:
            print(commit_line(log[i]))
    printed = {recs[i]["task"] for _, i, _ in scored[:n]} | {log[i]["hash"] for _, i, _ in found[:n]}
    gone = [(r["task"], brief_line(r), " ".join((r["title"], r["goal"], r["change"]))) for r in recs
            if says_removed(" ".join((r["title"], r["goal"], r["change"])))]
    gone += [(c["hash"], commit_line(c), c["subject"]) for c in log if says_removed(c["subject"])]
    gone = [g for g in gone if g[0] not in printed]
    hits = rank(query, [g[2] for g in gone])[:min(n, 5)]
    if hits:
        print("Removed or hidden before (Tasks and commits that say so); check these before you say that "
              "something exists or ask about it:")
        for _, i, _ in hits:
            print(gone[i][1])
    return min(len(scored), n) + min(len(found), n) + len(hits)


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
    brief = "--brief" in args
    args = [a for a in args if a != "--brief"]
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
        if not fold(" ".join(args)).split():
            print("worklog.py: search needs at least one word", file=sys.stderr)
            return 2
        search(recs, str(load_json(state).get("pane_path") or ""), " ".join(args), n, brief)
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
