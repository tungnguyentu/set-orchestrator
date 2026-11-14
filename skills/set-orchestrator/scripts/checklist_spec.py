#!/usr/bin/env python3
"""specHash: one fingerprint for everything the coordinator decided about a Task.

specHash = sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of
  {"taskId", "baseRef", "items": [{"id", "step"}], "protectedPaths", "acceptanceCommands"}
Worker-owned fields (passes, evidence, worker, originalGoal, version) are not part of it,
so mark-item.py never changes it, while any edit to step text, ids, protected paths,
acceptance commands, baseRef or taskId does.
"protectedBaseline" ({path: signature} of the protected files that were already uncommitted
when the Task was dispatched, written by init-checklist.py) is hashed too, but only when it
is present and not empty, so checklists created without it keep their hash.

The hash stored inside checklist.json is only a convenience: the worker can rewrite it.
The trusted copy is the one init-checklist.py prints, which the coordinator stores in its
state file (dispatches[].spec_hash) and passes to verify-checklist.py --expect-spec-hash.

CLI: checklist_spec.py <checklist.json>   prints the hash computed from the file contents.
"""
import fnmatch, hashlib, json, os, sys

SPEC_FIELDS = ("taskId", "baseRef", "items", "protectedPaths", "acceptanceCommands")


def canonical_spec(data):
    """The hashed view of a checklist dict. Missing fields hash as empty values."""
    items = data.get("items") or []
    spec = {
        "taskId": str(data.get("taskId") or ""),
        "baseRef": str(data.get("baseRef") or ""),
        "items": [{"id": str(i.get("id")), "step": str(i.get("step", ""))} for i in items if isinstance(i, dict)],
        "protectedPaths": [str(p) for p in (data.get("protectedPaths") or [])],
        "acceptanceCommands": [str(c) for c in (data.get("acceptanceCommands") or [])],
    }
    baseline = data.get("protectedBaseline")
    if isinstance(baseline, dict) and baseline:
        spec["protectedBaseline"] = {str(k): str(v) for k, v in baseline.items()}
    return spec


def norm(p):
    p = p.strip()
    return p[2:] if p.startswith("./") else p


def matches(path, pattern):
    """True when the repo-relative path falls under a protected glob or path prefix."""
    pat = norm(pattern)
    if not pat:
        return False
    if any(c in pat for c in "*?["):
        return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.rstrip("/") + "/*")
    pat = pat.rstrip("/")
    return path == pat or path.startswith(pat + "/")


BIG_FILE = 64 * 1024 * 1024  # larger files are compared by size and mtime instead of content


def path_signature(full):
    """Fingerprint of one worktree path: content hash, or a marker for absent and non-file paths."""
    try:
        if os.path.islink(full):
            return "link:" + os.readlink(full)
        if not os.path.exists(full):
            return "absent"
        if not os.path.isfile(full):
            return "notfile"
        st = os.stat(full)
        if st.st_size > BIG_FILE:
            return f"big:{st.st_size}:{st.st_mtime_ns}"
        h = hashlib.sha256()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError as e:
        return f"unreadable:{e.errno}"


def canonical_json(data):
    return json.dumps(canonical_spec(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_hash(data):
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def main(argv):
    if len(argv) != 2:
        print("usage: checklist_spec.py <checklist.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        print(spec_hash(json.load(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
