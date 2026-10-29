#!/usr/bin/env python3
"""Small helpers for dispatch.sh / settle.sh: the coordinator state file and Orca JSON.

  orch_state.py get STATE TASK_ID [FIELD]        print the dispatches[] entry (JSON) or one field
  orch_state.py field STATE FIELD                print a top-level field (pane_path, skill_family, ...)
  orch_state.py roster-title STATE HANDLE        print roster[].title for a terminal handle
  orch_state.py upsert STATE JSON                merge JSON into dispatches[] entry with the same task_id
  orch_state.py extract-id KIND < orca.json      print result.<KIND>.id (KIND = task | dispatch)
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
import hashlib, json, os, re, shutil, sys, time

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


def upsert(path, patch):
    lock = open(path + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(path)
        disp = state.setdefault("dispatches", [])
        cur = find_entry(state, patch["task_id"])
        if cur is None:
            disp.append(dict(patch))
        else:
            cur.update(patch)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        lock.close()


def set_top(path, key, value):
    """Set one top-level state field under the same lock as upsert."""
    lock = open(path + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(path)
        state[key] = value
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        lock.close()


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
