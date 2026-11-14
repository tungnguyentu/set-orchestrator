#!/usr/bin/env python3
"""Small helpers for dispatch.sh / settle.sh: the coordinator state file and Orca JSON.

  orch_state.py get STATE TASK_ID [FIELD]        print the dispatches[] entry (JSON) or one field
  orch_state.py field STATE FIELD                print a top-level field (pane_path, skill_family, ...)
  orch_state.py roster-title STATE HANDLE        print roster[].title for a terminal handle
  orch_state.py upsert STATE JSON                merge JSON into dispatches[] entry with the same task_id
  orch_state.py model-tier MODEL                 print the tier (light|standard|heavy|frontier) of a model name
  orch_state.py worker-info STATE HANDLE FIELD   print a roster[] field (title, model, effort) or tier for a handle
  orch_state.py tier-workers STATE TIER          print "title (model)" of each roster worker whose tier is TIER
  orch_state.py accept-frontier STATE TASK --quote "<user's words>"
                                                 record the user's yes for a frontier Task (frontier_accepted[TASK])
  orch_state.py frontier-accepted STATE TASK [FIELD]
                                                 print the acceptance record (JSON) or one field; exit 4 when none
  orch_state.py open-dispatches STATE HANDLE [--exclude TASK]
                                                 print one line per dispatch of the terminal still dispatched or
                                                 active (other than TASK); nothing when there is none
  orch_state.py abandon STATE --reason "<why>" --user-said "<the user's words>" (--task TASK | --terminal HANDLE | --all)
                                                 give up matching dispatched/active entries (abandoned_at,
                                                 abandon_reason, abandon_user_said). Not a way to close a finished
                                                 Task: that is settle.sh; prints {"abandoned": N, "tasks": [...]}; exit 2 on
                                                 an empty reason or not exactly one selector. Never calls Orca and
                                                 never touches an entry in any other status
  orch_state.py tasks-since-reset STATE HANDLE [--exclude TASK]
                                                 print how many dispatches the terminal started after its
                                                 context_resets time (all when none), failed sends not counted
  orch_state.py context-reset STATE HANDLE [--handoff PATH]
                                                 record context_resets[HANDLE] = {at, handoff}
  orch_state.py roster-set STATE HANDLE --model M [--effort E]
                                                 write the worker's new model (and effort) into its roster[] entry
  orch_state.py extract-id KIND < orca.json     print result.<KIND>.id (KIND = task | dispatch)
  orch_state.py parse-done-when SPEC_FILE        print the numbered Done-when items, one per line
  orch_state.py gate-snapshot STATE SCRIPTS_DIR [--accept-gate-change]
                                                 freeze the gate scripts into <state dir>/gate/<hash>/
                                                 (exit 5: skill scripts changed since the last snapshot)
  orch_state.py gate-check STATE HASH [--allow-gate-change]
                                                 exit 5 when the frozen copy or the skill dir changed

The state file is ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self>.json (outside the
worktree, so workers do not write it). Updates take an exclusive lock and replace atomically,
so parallel dispatch.sh calls in one wave do not lose entries.
"""
import datetime, hashlib, json, os, re, shutil, sys, time

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_entry(state, task_id):
    for d in state.get("dispatches") or []:
        if str(d.get("task_id")) == str(task_id):
            return d
    return None


def locked_update(path, mutate):
    """Load the state file under an exclusive lock, apply mutate(state), replace it atomically."""
    lock = open(path + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(path)
        mutate(state)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        lock.close()


def upsert(path, patch):
    def mutate(state):
        disp = state.setdefault("dispatches", [])
        cur = find_entry(state, patch["task_id"])
        if cur is None:
            disp.append(dict(patch))
        else:
            cur.update(patch)
    locked_update(path, mutate)


def set_top(path, key, value):
    """Set one top-level state field under the same lock as upsert."""
    locked_update(path, lambda state: state.__setitem__(key, value))


# ---- tiers and frontier acceptance -----------------------------------------------------------
# Tier of a worker, from the model recorded in roster[]. Tier meanings and the routing rules are in
# references/model-routing.md; keep this table in step with its "Tiers" table. First match wins.
# A missing, empty, "unknown" or unrecognized model (other agents such as codex or grok) is
# "standard".
MODEL_TIERS = (("haiku", "light"), ("sonnet", "standard"), ("opus", "heavy"), ("fable", "frontier"))


def model_tier(model):
    m = (model or "").lower()
    for needle, tier in MODEL_TIERS:
        if needle in m:
            return tier
    return "standard"


def roster_entry(state, handle):
    for r in state.get("roster") or []:
        if r.get("handle") == handle:
            return r
    return {}


def accept_frontier(path, task_id, quote):
    """Store the user's yes for one frontier Task: frontier_accepted[task_id] = {quote, at}."""
    if not str(quote or "").strip():
        raise ValueError("--quote must hold the user's words; an empty quote is not an acceptance")
    rec = {"quote": quote, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    locked_update(path, lambda state: state.setdefault("frontier_accepted", {}).__setitem__(str(task_id), rec))


# ---- open dispatches, abandon, context resets -------------------------------------------------
OPEN_STATUSES = ("dispatched", "active")
NOT_STARTED_STATUSES = ("dispatch_failed", "send_failed")  # the brief never reached the worker


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_ts(value):
    """Parse a timestamp of the state file into an aware datetime, or None.

    Both "+0700" and "+07:00" offsets occur in real state files; "Z" and fractional seconds are
    tolerated, a timestamp without an offset is read as local time."""
    s = str(value or "").strip()
    if not s:
        return None
    s = re.sub(r"Z$", "+0000", s)
    s = re.sub(r"([+-]\d\d):(\d\d)$", r"\1\2", s)
    s = re.sub(r"(\d\d:\d\d:\d\d)\.\d+", r"\1", s)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            t = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return t if t.tzinfo else t.astimezone()
    return None


def open_dispatches(state, handle, exclude=None):
    """Entries of the terminal that still await settlement, other than the Task `exclude`."""
    return [d for d in state.get("dispatches") or []
            if d.get("terminal") == handle and d.get("status") in OPEN_STATUSES
            and (exclude is None or str(d.get("task_id")) != str(exclude))]


def abandon(path, reason, task=None, terminal=None, every=False, user_said=None):
    """Mark open entries (dispatched or active) matching the selector as abandoned; return their Task ids.

    Abandon gives a Task up. It is not a way to close a finished one (that is settle.sh, which runs
    the gate), so it needs the user's own words for it."""
    if not str(reason or "").strip():
        raise ValueError("--reason must say why; an empty reason is not accepted")
    if not str(user_said or "").strip():
        raise ValueError("--user-said \"<the user's words>\" is required: abandon gives a Task up and needs "
                         "the user's word. A finished Task is closed with the frozen settle.sh, never with "
                         "abandon. When settle.sh fails for a reason that is not the worker's, stop and tell "
                         "the user")
    if sum((task is not None, terminal is not None, bool(every))) != 1:
        raise ValueError("give exactly one of --task, --terminal, --all")
    hit = []

    def mutate(state):
        for d in state.get("dispatches") or []:
            if d.get("status") not in OPEN_STATUSES:
                continue
            if task is not None and str(d.get("task_id")) != str(task):
                continue
            if terminal is not None and d.get("terminal") != terminal:
                continue
            d.update({"status": "abandoned", "abandoned_at": now_ts(), "abandon_reason": reason,
                      "abandon_user_said": user_said})
            hit.append(d.get("task_id"))
    locked_update(path, mutate)
    return hit


def tasks_since_reset(state, handle, exclude=None):
    """Number of dispatches that reached (or may have reached) the terminal since its last context reset.

    Entries whose brief was never sent are not counted. After a reset only entries that provably
    started later are counted (the times are compared as times, not strings)."""
    rec = (state.get("context_resets") or {}).get(handle) or {}
    reset = parse_ts(rec.get("at"))
    n = 0
    for d in state.get("dispatches") or []:
        if d.get("terminal") != handle or d.get("status") in NOT_STARTED_STATUSES:
            continue
        if exclude is not None and str(d.get("task_id")) == str(exclude):
            continue
        if reset is not None:
            t = parse_ts(d.get("started"))
            if t is None or t <= reset:
                continue
        n += 1
    return n


def context_reset(path, handle, handoff=None):
    """Record that the worker's context was cleared now: context_resets[handle] = {at, handoff}."""
    rec = {"at": now_ts(), "handoff": handoff or None}
    locked_update(path, lambda state: state.setdefault("context_resets", {}).__setitem__(handle, rec))


def roster_set(path, handle, model, effort=None):
    """Write the model (and effort) a worker now runs on into its roster[] entry."""
    found = []

    def mutate(state):
        r = roster_entry(state, handle)
        if r:
            r["model"] = model
            if effort:
                r["effort"] = effort
            found.append(True)
    locked_update(path, mutate)
    return bool(found)


# ---- gate snapshot ---------------------------------------------------------------------------
# Workers run as the same OS user in bypass mode, so they can edit this skill's scripts. The
# coordinator therefore freezes the gate scripts once (trust on first use, at the first
# dispatch.sh of a state file) into <state dir>/gate/<manifest hash>/ (read-only files), runs
# settle.sh / verify-checklist.py from that copy, and at every settle compares (a) the copy with
# its own hash and (b) the skill directory with the copy. Any difference fails the Task.
GATE_FILES = ("verify-checklist.py", "checklist_spec.py", "orch_state.py", "settle.sh", "dispatch.sh",
              "orca-done.sh", "mark-item.py", "write-report.py", "init-checklist.py")


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def gate_manifest(d):
    missing = [n for n in GATE_FILES if not os.path.isfile(os.path.join(d, n))]
    if missing:
        raise FileNotFoundError(f"gate files missing in {d}: {', '.join(missing)}")
    return {n: file_sha(os.path.join(d, n)) for n in GATE_FILES}


def manifest_hash(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def gate_dir(state_path, h):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), "gate", h)


def changed(a, b):
    return sorted(n for n in GATE_FILES if a.get(n) != b.get(n))


def gate_snapshot(state_path, src, accept_change=False):
    """Freeze src's gate scripts; return (exit code, result dict)."""
    src = os.path.abspath(src)
    m = gate_manifest(src)
    h = manifest_hash(m)
    prev = load(state_path).get("gate") or {}
    if prev.get("hash") and prev["hash"] != h and not accept_change:
        diff = changed(prev.get("files") or {}, m)
        return 5, {"ok": False, "error": "gate scripts in the skill directory changed since the snapshot "
                   f"{prev['hash'][:12]} ({', '.join(diff)}). If you changed them yourself, rerun with "
                   "--accept-gate-change; otherwise a worker edited them: inspect the diff first."}
    dst = gate_dir(state_path, h)
    if os.path.isdir(dst):
        got = gate_manifest(dst)
        if manifest_hash(got) != h:
            return 6, {"ok": False, "error": f"gate snapshot {dst} was modified ({', '.join(changed(m, got))})"}
    else:
        tmp = f"{dst}.tmp.{os.getpid()}"
        os.makedirs(tmp)
        for n in GATE_FILES:
            shutil.copy2(os.path.join(src, n), os.path.join(tmp, n))
            os.chmod(os.path.join(tmp, n), 0o444)
        os.replace(tmp, dst)
        os.chmod(dst, 0o555)
    if prev.get("hash") != h:
        set_top(state_path, "gate", {"hash": h, "dir": dst, "source": src, "files": m,
                                     "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return 0, {"ok": True, "hash": h, "dir": dst, "settle": os.path.join(dst, "settle.sh")}


def gate_check(state_path, h, allow_source_change=False):
    """Check the frozen copy h against its own hash and the skill dir against the copy."""
    dst = gate_dir(state_path, h)
    res = {"ok": True, "hash": h, "dir": dst, "errors": [], "warnings": []}
    try:
        got = gate_manifest(dst)
    except FileNotFoundError as e:
        res["errors"].append(str(e))
        got = None
    if got is not None and manifest_hash(got) != h:
        res["errors"].append(f"gate snapshot {dst} was modified after dispatch")
    src = (load(state_path).get("gate") or {}).get("source")
    if got is not None and src:
        try:
            diff = changed(got, gate_manifest(src))
        except FileNotFoundError as e:
            diff = [str(e)]
        if diff:
            msg = (f"gate scripts in the skill directory {src} differ from the snapshot used at dispatch: "
                   f"{', '.join(diff)} (the worker-side gate may have been edited)")
            (res["warnings"] if allow_source_change else res["errors"]).append(msg)
    res["ok"] = not res["errors"]
    return (0 if res["ok"] else 5), res


def extract_id(obj, kind):
    """Find the id of a task or dispatch in an Orca --json result, tolerating shape changes."""
    res = obj.get("result", obj) if isinstance(obj, dict) else obj
    if isinstance(res, dict):
        sub = res.get(kind)
        if isinstance(sub, dict) and sub.get("id"):
            return sub["id"]
        for k in (f"{kind}Id", f"{kind}_id"):
            if res.get(k):
                return res[k]
    prefixes = {"task": ("task_",), "dispatch": ("ctx_", "dispatch_", "dsp_")}[kind]

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, str) and o.startswith(prefixes):
            return o
        return None
    return walk(res)


def parse_done_when(text):
    lines = text.splitlines()
    out, inside = [], False
    for ln in lines:
        if re.match(r"^\s*(#+\s*)?\**done[- ]when\**\s*[:(]?", ln, re.I):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s*(\d+)[.)]\s+(.*\S)", ln)
            if m:
                out.append(m.group(2))
            elif ln.strip() and out and not ln.startswith((" ", "\t")):
                break
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "get":
        e = find_entry(load(argv[2]), argv[3])
        if e is None:
            return 3
        if len(argv) > 4:
            v = e.get(argv[4])
            if v is None:
                return 4
            print(v if not isinstance(v, (dict, list)) else json.dumps(v))
        else:
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if cmd == "field":
        v = load(argv[2]).get(argv[3])
        if v is None:
            return 4
        print(v if not isinstance(v, (dict, list)) else json.dumps(v))
        return 0
    if cmd == "roster-title":
        for r in load(argv[2]).get("roster") or []:
            if r.get("handle") == argv[3] and r.get("title"):
                print(r["title"])
                return 0
        return 4
    if cmd == "upsert":
        patch = json.loads(argv[3])
        if not patch.get("task_id"):
            print("upsert: task_id required", file=sys.stderr)
            return 2
        upsert(argv[2], patch)
        return 0
    if cmd == "model-tier":
        print(model_tier(argv[2] if len(argv) > 2 else ""))
        return 0
    if cmd == "worker-info":
        r = roster_entry(load(argv[2]), argv[3])
        print(model_tier(r.get("model")) if argv[4] == "tier" else r.get(argv[4]) or "")
        return 0
    if cmd == "tier-workers":
        for r in load(argv[2]).get("roster") or []:
            if model_tier(r.get("model")) == argv[3]:
                print(f"{r.get('title') or r.get('handle')} ({r.get('model') or 'unknown'})")
        return 0
    if cmd == "accept-frontier":
        i = argv.index("--quote") if "--quote" in argv else -1
        try:
            accept_frontier(argv[2], argv[3], argv[i + 1] if 0 <= i < len(argv) - 1 else "")
        except ValueError as e:
            print(f"accept-frontier: {e}", file=sys.stderr)
            return 2
        return 0
    if cmd == "frontier-accepted":
        rec = (load(argv[2]).get("frontier_accepted") or {}).get(str(argv[3]))
        if not rec:
            return 4
        print(rec.get(argv[4], "") if len(argv) > 4 else json.dumps(rec, ensure_ascii=False))
        return 0
    if cmd == "open-dispatches":
        ex = argv[argv.index("--exclude") + 1] if "--exclude" in argv[4:] else None
        for d in open_dispatches(load(argv[2]), argv[3], ex):
            print(f"{d.get('task_id')} ({d.get('title') or 'no title yet'}), status {d.get('status')}, "
                  f"started {d.get('started') or 'unknown'}")
        return 0
    if cmd == "abandon":
        opts = {"--reason": None, "--task": None, "--terminal": None, "--user-said": None}
        for k in opts:
            if k in argv[3:]:
                i = argv.index(k, 3)
                opts[k] = argv[i + 1] if i + 1 < len(argv) else ""
        try:
            hit = abandon(argv[2], opts["--reason"], opts["--task"], opts["--terminal"], "--all" in argv[3:],
                          opts["--user-said"])
        except ValueError as e:
            print(f"abandon: {e}", file=sys.stderr)
            return 2
        print(json.dumps({"abandoned": len(hit), "tasks": hit}))
        return 0
    if cmd == "tasks-since-reset":
        ex = argv[argv.index("--exclude") + 1] if "--exclude" in argv[4:] else None
        print(tasks_since_reset(load(argv[2]), argv[3], ex))
        return 0
    if cmd == "context-reset":
        i = argv.index("--handoff") if "--handoff" in argv[4:] else -1
        context_reset(argv[2], argv[3], argv[i + 1] if 0 <= i < len(argv) - 1 else None)
        return 0
    if cmd == "roster-set":
        opts = {"--model": "", "--effort": ""}
        for k in opts:
            if k in argv[4:]:
                i = argv.index(k, 4)
                opts[k] = argv[i + 1] if i + 1 < len(argv) else ""
        if not opts["--model"]:
            print("roster-set: --model is required", file=sys.stderr)
            return 2
        if not roster_set(argv[2], argv[3], opts["--model"], opts["--effort"]):
            print(f"roster-set: no roster entry for {argv[3]}", file=sys.stderr)
            return 3
        return 0
    if cmd == "extract-id":
        try:
            obj = json.loads(sys.stdin.read())
        except Exception:
            return 3
        v = extract_id(obj, argv[2])
        if not v:
            return 3
        print(v)
        return 0
    if cmd == "parse-done-when":
        for it in parse_done_when(open(argv[2], encoding="utf-8").read()):
            print(it)
        return 0
    if cmd == "gate-snapshot":
        rc, res = gate_snapshot(argv[2], argv[3], "--accept-gate-change" in argv[4:])
        print(json.dumps(res, ensure_ascii=False))
        return rc
    if cmd == "gate-check":
        rc, res = gate_check(argv[2], argv[3], "--allow-gate-change" in argv[4:])
        print(json.dumps(res, ensure_ascii=False))
        return rc
    if cmd == "now":
        print(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        return 0
    print(f"unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
