#!/usr/bin/env python3
"""Gate: is every Done-when item of a Task really done?

Usage:
  verify-checklist.py --checklist PATH [--require-report PATH] [--protected-diff]
                      [--allow-protected PATH ...] [--rerun-acceptance]
                      [--cwd DIR] [--base REF] [--timeout SEC]

Exit 0: every item has passes==true and non-empty evidence, and every optional check passed.
Exit 2: something is missing; the misses are listed on stderr.
Stdout always carries one JSON summary line.

--protected-diff   collects changed paths (vs --base or the checklist's baseRef, plus staged,
                   unstaged and untracked files) and fails when one matches protectedPaths
                   (glob or path prefix) and is not given with --allow-protected.
--rerun-acceptance runs every acceptanceCommands entry with sh -c in --cwd and requires exit 0.
Agent-neutral: used by orca-done.sh (all agents), the coordinator, and optional Claude Stop hooks.
"""
import argparse, fnmatch, hashlib, json, os, subprocess, sys


def steps_hash(items):
    raw = json.dumps([[str(i.get("id")), i.get("step", "")] for i in items], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run(cmd, cwd, timeout=60):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def git_lines(args, cwd):
    try:
        rc, out, _ = run(["git", *args], cwd)
    except Exception:
        return None
    return [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else None


def norm(p):
    p = p.strip()
    return p[2:] if p.startswith("./") else p


def matches(path, pattern):
    pat = norm(pattern)
    if not pat:
        return False
    if any(c in pat for c in "*?["):
        return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.rstrip("/") + "/*")
    pat = pat.rstrip("/")
    return path == pat or path.startswith(pat + "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checklist", required=True)
    ap.add_argument("--require-report")
    ap.add_argument("--protected-diff", action="store_true")
    ap.add_argument("--allow-protected", action="append", default=[])
    ap.add_argument("--rerun-acceptance", action="store_true")
    ap.add_argument("--cwd", help="repo dir for git and acceptance commands (default: git toplevel of cwd)")
    ap.add_argument("--base", help="git ref to diff against (default: checklist baseRef)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per acceptance command")
    a = ap.parse_args()

    summary = {"ok": False, "checklist": os.path.abspath(a.checklist), "taskId": None,
               "total": 0, "passed": 0, "misses": [], "protectedViolations": [],
               "acceptance": [], "report": None, "errors": []}
    errs = []

    try:
        with open(a.checklist, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        errs.append(f"cannot read checklist {a.checklist}: {e}")
        data = None

    if data is not None:
        items = data.get("items") or []
        summary["taskId"] = data.get("taskId")
        summary["total"] = len(items)
        if not items:
            errs.append("checklist has no items")
        for it in items:
            ok = it.get("passes") is True and str(it.get("evidence", "")).strip() != ""
            if ok:
                summary["passed"] += 1
            else:
                why = "passes is not true" if it.get("passes") is not True else "evidence is empty"
                summary["misses"].append({"id": str(it.get("id")), "step": it.get("step", ""), "why": why})
                errs.append(f"item {it.get('id')} not done ({why}): {it.get('step', '')}")
        if data.get("stepsHash") and data["stepsHash"] != steps_hash(items):
            errs.append("checklist step text or ids were changed after init (stepsHash mismatch)")

    if a.require_report:
        summary["report"] = os.path.abspath(a.require_report)
        if not os.path.isfile(a.require_report) or os.path.getsize(a.require_report) == 0:
            errs.append(f"report missing or empty: {a.require_report}")
        elif a.require_report.endswith(".json"):
            try:
                json.load(open(a.require_report, encoding="utf-8"))
            except Exception as e:
                errs.append(f"report is not valid JSON: {e}")

    cwd = a.cwd
    if not cwd:
        top = git_lines(["rev-parse", "--show-toplevel"], os.getcwd())
        cwd = top[0] if top else os.getcwd()

    if a.protected_diff and data is not None:
        prot = data.get("protectedPaths") or []
        if prot:
            base = a.base or data.get("baseRef") or ""
            changed = set()
            for args in (["diff", "--name-only", base] if base else ["diff", "--name-only", "HEAD"],
                         ["diff", "--name-only", "--cached"],
                         ["diff", "--name-only"],
                         ["ls-files", "--others", "--exclude-standard"]):
                got = git_lines(args, cwd)
                if got is None:
                    if args[0] == "diff" and len(args) == 3 and base:
                        errs.append(f"git diff against base {base} failed (not a repo or unknown ref)")
                    continue
                changed.update(got)
            allow = {norm(p).rstrip("/") for p in a.allow_protected}
            for path in sorted(changed):
                if path.startswith(".orca/"):
                    continue
                hit = next((p for p in prot if matches(path, p)), None)
                if hit and not any(path == al or path.startswith(al + "/") for al in allow):
                    summary["protectedViolations"].append({"path": path, "pattern": hit})
                    errs.append(f"protected path changed: {path} (matches {hit}); pass --allow-protected {path} only with coordinator approval")

    if a.rerun_acceptance and data is not None:
        for cmd in data.get("acceptanceCommands") or []:
            try:
                r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=a.timeout)
                rc, tail = r.returncode, (r.stdout + r.stderr)[-600:]
            except subprocess.TimeoutExpired:
                rc, tail = 124, f"timeout after {a.timeout}s"
            summary["acceptance"].append({"command": cmd, "exitCode": rc})
            if rc != 0:
                errs.append(f"acceptance command failed (exit {rc}): {cmd}\n    ...{tail.strip()[-400:]}")

    summary["errors"] = errs
    summary["ok"] = not errs
    print(json.dumps(summary, ensure_ascii=False))
    if errs:
        print("verify-checklist: FAIL", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
