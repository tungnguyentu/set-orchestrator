#!/usr/bin/env python3
"""Create or update the long-form report of a Task (the worker_done body stays short).

File: .orca/tasks/<taskId>/report.json (or --report PATH)
  {"taskId", "dispatchId", "steps": [{"id", "commands": [], "exitCode", "notes", "outputTail"}], "summary"}

Usage:
  write-report.py --task T [--dispatch D] [--report PATH] --step 2 --command "npm test" --exit-code 0 --notes "41 passed"
  write-report.py --task T --step 3 --run "npm run build"      # runs it, records command, exit code, output tail
  write-report.py --task T --summary "two-sentence summary"
Repeated calls merge: commands are appended, exitCode/notes overwritten when given.
With --run the script exits with the command's exit code, so a failure is visible.
"""
import argparse, json, os, subprocess, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--dispatch", default=None)
    ap.add_argument("--report")
    ap.add_argument("--step")
    ap.add_argument("--command", action="append", default=[])
    ap.add_argument("--run", help="shell command to execute and record (in the cwd)")
    ap.add_argument("--exit-code", type=int)
    ap.add_argument("--notes")
    ap.add_argument("--summary")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    path = a.report or os.path.join(".orca", "tasks", a.task, "report.json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = {"taskId": a.task, "dispatchId": a.dispatch or "", "steps": [], "summary": ""}
    if os.path.exists(path):
        try:
            data.update(json.load(open(path, encoding="utf-8")))
        except Exception as e:
            print(f"write-report: existing {path} is not valid JSON ({e}); refusing to overwrite", file=sys.stderr)
            return 2
    data["taskId"] = a.task
    if a.dispatch:
        data["dispatchId"] = a.dispatch

    rc_out = 0
    if a.step is not None or a.run or a.command:
        if a.step is None:
            print("write-report: --step is required with --command/--run", file=sys.stderr)
            return 2
        step = next((s for s in data["steps"] if str(s.get("id")) == str(a.step)), None)
        if step is None:
            step = {"id": str(a.step), "commands": [], "exitCode": None, "notes": ""}
            data["steps"].append(step)
        step.setdefault("commands", []).extend(a.command)
        if a.exit_code is not None:
            step["exitCode"] = a.exit_code
        if a.run:
            try:
                r = subprocess.run(a.run, shell=True, capture_output=True, text=True, timeout=a.timeout)
                rc_out, tail = r.returncode, (r.stdout + r.stderr)[-1500:]
            except subprocess.TimeoutExpired:
                rc_out, tail = 124, f"timeout after {a.timeout}s"
            step["commands"].append(a.run)
            step["exitCode"] = rc_out
            step["outputTail"] = tail
        if a.notes is not None:
            step["notes"] = a.notes
        data["steps"].sort(key=lambda s: (len(str(s.get("id"))), str(s.get("id"))))
    if a.summary is not None:
        data["summary"] = a.summary

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    print(json.dumps({"ok": rc_out == 0, "report": os.path.abspath(path), "step": a.step,
                      "exitCode": rc_out if a.run else a.exit_code}))
    return rc_out


if __name__ == "__main__":
    sys.exit(main())
