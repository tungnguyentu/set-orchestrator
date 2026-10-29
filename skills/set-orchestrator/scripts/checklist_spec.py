#!/usr/bin/env python3
"""specHash: one fingerprint for everything the coordinator decided about a Task.

specHash = sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of
  {"taskId", "baseRef", "items": [{"id", "step"}], "protectedPaths", "acceptanceCommands"}
Worker-owned fields (passes, evidence, worker, originalGoal, version) are not part of it,
so mark-item.py never changes it, while any edit to step text, ids, protected paths,
acceptance commands, baseRef or taskId does.

The hash stored inside checklist.json is only a convenience: the worker can rewrite it.
The trusted copy is the one init-checklist.py prints, which the coordinator stores in its
state file (dispatches[].spec_hash) and passes to verify-checklist.py --expect-spec-hash.

CLI: checklist_spec.py <checklist.json>   prints the hash computed from the file contents.
"""
import hashlib, json, sys

SPEC_FIELDS = ("taskId", "baseRef", "items", "protectedPaths", "acceptanceCommands")


def canonical_spec(data):
    """The hashed view of a checklist dict. Missing fields hash as empty values."""
    items = data.get("items") or []
    return {
        "taskId": str(data.get("taskId") or ""),
        "baseRef": str(data.get("baseRef") or ""),
        "items": [{"id": str(i.get("id")), "step": str(i.get("step", ""))} for i in items if isinstance(i, dict)],
        "protectedPaths": [str(p) for p in (data.get("protectedPaths") or [])],
        "acceptanceCommands": [str(c) for c in (data.get("acceptanceCommands") or [])],
    }


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
