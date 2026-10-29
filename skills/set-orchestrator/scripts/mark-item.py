#!/usr/bin/env python3
"""Mark one checklist item as passed (or not) with evidence. Any agent can call it.

Usage:
  mark-item.py --checklist PATH --id N --evidence "<cmd -> exit 0 | commit <sha> | path/file.ext:LINE>" [--fail]
  mark-item.py --checklist PATH --id N --run "<command>" [--evidence "<extra note>"] [--expect-fail]

--run executes the command with sh -c in the cwd and records "<command> -> exit N" as evidence
(plus the --evidence note). The item passes only when the command exits 0 (with --expect-fail:
only when it exits non-zero, for a "write a failing test first" step). --fail always keeps it open.
The script exits with 0 when the item ends up passing, 1 when it does not, 2 on bad input.

Only `passes` and `evidence` of that item change; step text, ids and specHash are untouched.
verify-checklist.py checks evidence: "commit <sha>" must exist, file:line must exist and be in
range, "-> exit N" with N != 0 fails unless it ends with "(expected fail)"; a typed
"<cmd> -> exit 0" that is not an acceptance command, and plain text, are flagged "unverified".
"""
import argparse, json, os, subprocess, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checklist", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--evidence", default="")
    ap.add_argument("--run", help="command to execute; its exit code becomes the evidence")
    ap.add_argument("--expect-fail", action="store_true", help="with --run: pass when the command exits non-zero")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--fail", action="store_true", help="record evidence but keep passes=false")
    a = ap.parse_args()

    passes = not a.fail
    evidence = a.evidence.strip()
    if a.run:
        try:
            r = subprocess.run(a.run, shell=True, capture_output=True, text=True, timeout=a.timeout)
            rc = r.returncode
            sys.stderr.write((r.stdout + r.stderr)[-2000:])
        except subprocess.TimeoutExpired:
            rc = 124
        ok = (rc != 0) if a.expect_fail else (rc == 0)
        passes = passes and ok
        evidence = (f"{a.run.strip()} -> exit {rc}" + (" (expected fail)" if a.expect_fail and rc != 0 else "")
                    + (f" ({evidence})" if evidence else ""))
    if not evidence:
        print("mark-item: --evidence (or --run) must not be empty", file=sys.stderr)
        return 2

    with open(a.checklist, encoding="utf-8") as f:
        data = json.load(f)
    for it in data.get("items", []):
        if str(it.get("id")) == str(a.id):
            it["passes"] = passes
            it["evidence"] = evidence
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
    print(json.dumps({"ok": True, "id": str(a.id), "passes": passes, "evidence": evidence,
                      "progress": f"{done}/{len(data['items'])}"}, ensure_ascii=False))
    return 0 if passes or a.fail else 1


if __name__ == "__main__":
    sys.exit(main())
