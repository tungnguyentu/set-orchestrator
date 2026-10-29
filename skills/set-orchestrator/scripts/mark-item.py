#!/usr/bin/env python3
"""Mark one checklist item as passed (or not) with evidence. Any agent can call it.

Usage: mark-item.py --checklist PATH --id N --evidence "<command -> result | commit | file:line>" [--fail]
Only `passes` and `evidence` of that item change; step text, ids and stepsHash are untouched.
"""
import argparse, json, os, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checklist", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--fail", action="store_true", help="record evidence but keep passes=false")
    a = ap.parse_args()
    if not a.evidence.strip():
        print("mark-item: --evidence must not be empty", file=sys.stderr)
        return 2
    with open(a.checklist, encoding="utf-8") as f:
        data = json.load(f)
    for it in data.get("items", []):
        if str(it.get("id")) == str(a.id):
            it["passes"] = not a.fail
            it["evidence"] = a.evidence.strip()
            break
    else:
        print(f"mark-item: no item with id {a.id} in {a.checklist}", file=sys.stderr)
        return 2
    tmp = a.checklist + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, a.checklist)
    done = sum(1 for i in data["items"] if i.get("passes") and str(i.get("evidence", "")).strip())
    print(json.dumps({"ok": True, "id": str(a.id), "passes": not a.fail, "progress": f"{done}/{len(data['items'])}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
