#!/usr/bin/env python3
"""Gate: is every Done-when item of a Task really done?

Usage:
  verify-checklist.py --checklist PATH [--expect-spec-hash H | --spec SPEC.json]
                      [--require-report PATH] [--protected-diff] [--allow-protected PATH ...]
                      [--rerun-acceptance] [--cwd DIR] [--base REF] [--timeout SEC]

Exit 0: spec integrity holds, every item has passes==true and evidence that is not
        contradicted, and every optional check passed.
Exit 2: something is missing or wrong; the misses are listed on stderr.
Stdout always carries one JSON summary line.

Spec integrity (always, checked FIRST): specHash is recomputed from the file contents
(taskId, baseRef, items[id, step], protectedPaths, acceptanceCommands; see checklist_spec.py).
  --expect-spec-hash H  trusted value from the coordinator's state file; the computed hash must
                        equal H. The hash field stored in the checklist is ignored.
  --spec SPEC.json      coordinator-side copy written by init-checklist.py --spec-out; its
                        specHash is used as H (and must agree with --expect-spec-hash if both).
  neither               the stored specHash must be present and equal to the computed one
                        (self-attested: a worker can recompute it, so the coordinator must
                        always pass --expect-spec-hash). Missing field = fail.
When integrity fails, protected-diff and acceptance commands are NOT run (they come from a file
the worker can write, and acceptance runs with shell=True).

Evidence (per item marked passes=true):
  commit <sha>                       must exist in the repo (git cat-file)   -> fail if not
  bare 7-40 hex token                counted when it is a commit; ignored otherwise (docker ids...)
  path/file.ext:LINE[-END]           file must exist, lines within range      -> fail if not
                                     (no "/" in the path: only known code/doc extensions, so
                                     host:port such as 127.0.0.1:8080 is not a file)
  <cmd> -> exit N, N != 0            fail, unless suffixed "(expected fail)" (mark-item --expect-fail)
  <cmd> -> exit 0                    verified only when <cmd> is an acceptance command re-run here
                                     (--rerun-acceptance); otherwise a "claim": unverified warning
  anything else (plain text)         "unverified" warning in the JSON summary -> not a failure

--protected-diff   collects changed paths (vs --base or the checklist's baseRef, plus staged,
                   unstaged and untracked files) and fails when one matches protectedPaths
                   (glob or path prefix) and is not given with --allow-protected. It sees the
                   WHOLE worktree: concurrent Tasks in one worktree trip each other, so only run
                   Tasks in parallel when their Ownership and Protected paths do not overlap.
--rerun-acceptance runs every acceptanceCommands entry with sh -c in --cwd and requires exit 0.
Agent-neutral: used by orca-done.sh (all agents), settle.sh / the coordinator, and optional
Claude Stop hooks.
"""
import argparse, fnmatch, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checklist_spec import spec_hash  # noqa: E402


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


# ---- evidence classification -------------------------------------------------------------
COMMIT_KW = re.compile(r"\bcommit(?:ted)?\b[\s:#=]*([0-9a-fA-F]{7,40})\b", re.I)
BARE_SHA = re.compile(r"(?<![\w/.:-])([0-9a-f]{7,40})(?![\w/.-])")
FILE_LINE = re.compile(r"(?<![\w/.@-])((?:[\w.\-]+/)*[\w\-][\w.\-]*\.[A-Za-z0-9_]+|(?:[\w.\-]+/)+[\w.\-]+):(\d+)(?:-(\d+))?(?![\w/])")
CMD_EXIT = re.compile(r"->\s*exit(?:\s*code)?\s*[:=]?\s*(-?\d+)(\s*\(expected fail\))?", re.I)
EXPECTED_FAIL = "(expected fail)"  # suffix written by mark-item.py --run --expect-fail
# A path without "/" counts as a file reference only with one of these extensions, so
# "127.0.0.1:8080" or "api.internal:443" are not read as file:line.
CODE_EXTS = {"py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "md", "mdx", "yml", "yaml", "toml",
             "ini", "cfg", "go", "rs", "java", "kt", "kts", "swift", "rb", "php", "c", "h", "cc", "cpp",
             "hpp", "cs", "m", "mm", "sh", "bash", "zsh", "sql", "html", "css", "scss", "less", "vue",
             "svelte", "txt", "xml", "gradle", "lock", "env", "dart", "lua", "ex", "exs", "erl", "scala"}


def norm_cmd(c):
    return " ".join(c.split())


def classify_evidence(ev, cwd, in_repo, rerun_cmds=()):
    """Return {kinds, problems, verified, refs}. problems => fail; verified False => warning.

    verified means at least one reference was checked against the repo (an existing commit, a
    file:line in range) or names an acceptance command that this run re-executes. A hand-written
    "<cmd> -> exit 0" for any other command is only a claim: kind "claim", not verified.
    """
    kinds, problems, refs, checked = [], [], [], False
    rerun = {norm_cmd(c) for c in rerun_cmds}
    shas = set(m.group(1).lower() for m in COMMIT_KW.finditer(ev))  # "commit <sha>": must exist
    bare = set()
    for m in BARE_SHA.finditer(ev):
        tok = m.group(1).lower()
        if tok not in shas and re.search(r"[0-9]", tok) and re.search(r"[a-f]", tok):
            bare.add(tok)  # could be any hex id (docker, uuid part): counts only if it is a commit
    for sha in sorted(shas | bare):
        if not in_repo:
            if sha in shas:
                kinds.append("commit")
                refs.append(f"commit {sha}")
            continue  # cannot check outside a repo; not a failure
        try:
            rc, _, _ = run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd)
        except Exception:
            rc = 1
        if rc == 0:
            kinds.append("commit")
            refs.append(f"commit {sha}")
            checked = True
        elif sha in shas:
            kinds.append("commit")
            refs.append(f"commit {sha}")
            problems.append(f"commit {sha} does not exist in {cwd}")
    for m in FILE_LINE.finditer(ev):
        path, start, end = m.group(1), int(m.group(2)), m.group(3)
        if "://" in ev[max(0, m.start() - 3):m.start() + 3]:
            continue
        if "/" not in path and path.rsplit(".", 1)[-1].lower() not in CODE_EXTS:
            continue
        kinds.append("file")
        refs.append(f"{path}:{start}" + (f"-{end}" if end else ""))
        full = path if os.path.isabs(path) else os.path.join(cwd, norm(path))
        if not os.path.isfile(full):
            problems.append(f"file in evidence not found: {path}")
            continue
        try:
            with open(full, "rb") as f:
                n = sum(1 for _ in f)
        except OSError as e:
            problems.append(f"cannot read {path}: {e}")
            continue
        last = int(end) if end else start
        if start < 1 or last < start or last > n:
            problems.append(f"line {start}{'-' + end if end else ''} out of range for {path} ({n} lines)")
        else:
            checked = True
    for m in CMD_EXIT.finditer(ev):
        code, expected_fail = int(m.group(1)), bool(m.group(2))
        cmd = norm_cmd(re.split(r"[;\n]", ev[:m.start()])[-1])
        refs.append(f"{cmd} -> exit {code}")
        if code != 0 and not expected_fail:
            kinds.append("command")
            problems.append(f"command evidence shows a failure: '{cmd} -> exit {code}' "
                            f"(a failing command cannot pass an item; use mark-item.py --run ... --expect-fail "
                            f"only when the step asks for a failing result)")
        elif cmd in rerun:
            kinds.append("command")
            checked = True  # re-executed below as an acceptance command
        else:
            kinds.append("claim")  # typed by the worker; nobody re-runs it
    return {"kinds": sorted(set(kinds)) or ["text"], "problems": problems,
            "verified": checked and not problems, "refs": refs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checklist", required=True)
    ap.add_argument("--expect-spec-hash", help="trusted specHash from the coordinator's state file")
    ap.add_argument("--spec", help="coordinator-side spec copy (init-checklist.py --spec-out)")
    ap.add_argument("--require-report")
    ap.add_argument("--protected-diff", action="store_true")
    ap.add_argument("--allow-protected", action="append", default=[])
    ap.add_argument("--rerun-acceptance", action="store_true")
    ap.add_argument("--cwd", help="repo dir for git and acceptance commands (default: git toplevel of cwd)")
    ap.add_argument("--base", help="git ref to diff against (default: checklist baseRef)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per acceptance command")
    a = ap.parse_args()

    summary = {"ok": False, "checklist": os.path.abspath(a.checklist), "taskId": None,
               "specHash": None, "expectedSpecHash": None, "specIntegrity": None,
               "total": 0, "passed": 0, "misses": [], "evidence": [], "unverifiedItems": [],
               "warnings": [], "protectedViolations": [], "acceptance": [], "report": None, "errors": []}
    errs, warns = [], summary["warnings"]

    def finish():
        summary["errors"] = errs
        summary["ok"] = not errs
        print(json.dumps(summary, ensure_ascii=False))
        if errs:
            print("verify-checklist: FAIL", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return 2
        return 0

    try:
        with open(a.checklist, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
    except Exception as e:
        errs.append(f"cannot read checklist {a.checklist}: {e}")
        summary["specIntegrity"] = "unreadable"
        return finish()

    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        errs.append("checklist items is not a list of objects")
        summary["specIntegrity"] = "malformed"
        return finish()

    # ---- 1. spec integrity, before anything that reads protectedPaths or runs commands ----
    expected = (a.expect_spec_hash or "").strip().lower() or None
    if a.spec:
        try:
            spec_expected = str(json.load(open(a.spec, encoding="utf-8")).get("specHash") or "").lower()
        except Exception as e:
            spec_expected = ""
            errs.append(f"cannot read coordinator spec {a.spec}: {e}")
        if not spec_expected:
            errs.append(f"coordinator spec {a.spec} has no specHash")
        elif expected and expected != spec_expected:
            errs.append("--expect-spec-hash and --spec disagree")
        expected = expected or spec_expected or None
    computed = spec_hash(data)
    stored = str(data.get("specHash") or "").strip().lower()
    summary["specHash"], summary["expectedSpecHash"], summary["taskId"] = computed, expected, data.get("taskId")
    if expected:
        if computed != expected:
            errs.append("spec tampered: specHash computed from checklist contents does not match the coordinator's "
                        "expected value (step text, ids, protectedPaths, acceptanceCommands, baseRef or taskId changed)")
        if stored and stored != computed:
            warns.append("stored specHash field does not match the contents (ignored; computed hash was used)")
    else:
        warns.append("no --expect-spec-hash: integrity is self-attested by the checklist file; "
                     "the coordinator must verify with --expect-spec-hash from its state file")
        if not stored:
            errs.append("specHash field missing from checklist" + (" (legacy stepsHash only: re-init with init-checklist.py --force)"
                                                                     if data.get("stepsHash") else "") + "; pass --expect-spec-hash")
        elif stored != computed:
            errs.append("specHash mismatch: checklist spec was changed after init")
    integrity_ok = not errs
    # acceptance commands count as verified evidence only when this run really re-executes them
    rerun_cmds = (data.get("acceptanceCommands") or []) if (integrity_ok and a.rerun_acceptance) else []
    summary["specIntegrity"] = "ok" if integrity_ok else "failed"

    # ---- 2. items and evidence ----
    cwd = a.cwd
    if not cwd:
        top = git_lines(["rev-parse", "--show-toplevel"], os.getcwd())
        cwd = top[0] if top else os.getcwd()
    in_repo = git_lines(["rev-parse", "--git-dir"], cwd) is not None

    summary["total"] = len(items)
    if not items:
        errs.append("checklist has no items")
    for it in items:
        iid, step = str(it.get("id")), it.get("step", "")
        ev = str(it.get("evidence", "")).strip()
        if it.get("passes") is not True or not ev:
            why = "passes is not true" if it.get("passes") is not True else "evidence is empty"
            summary["misses"].append({"id": iid, "step": step, "why": why})
            errs.append(f"item {iid} not done ({why}): {step}")
            continue
        c = classify_evidence(ev, cwd, in_repo, rerun_cmds)
        summary["evidence"].append({"id": iid, **c})
        if c["problems"]:
            summary["misses"].append({"id": iid, "step": step, "why": "; ".join(c["problems"])})
            errs.append(f"item {iid} evidence does not hold: {'; '.join(c['problems'])}")
            continue
        summary["passed"] += 1
        if not c["verified"]:
            summary["unverifiedItems"].append(iid)
            warns.append(f"item {iid} evidence is plain text (unverified; check it yourself): {ev[:120]}")

    if a.require_report:
        summary["report"] = os.path.abspath(a.require_report)
        if not os.path.isfile(a.require_report) or os.path.getsize(a.require_report) == 0:
            errs.append(f"report missing or empty: {a.require_report}")
        elif a.require_report.endswith(".json"):
            try:
                json.load(open(a.require_report, encoding="utf-8"))
            except Exception as e:
                errs.append(f"report is not valid JSON: {e}")

    if not integrity_ok:
        if a.protected_diff:
            warns.append("protected-diff skipped: spec integrity failed")
        if a.rerun_acceptance:
            warns.append("acceptance commands NOT run: spec integrity failed")
        return finish()

    # ---- 3. protected paths ----
    if a.protected_diff:
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
                    errs.append(f"protected path changed: {path} (matches {hit}); pass --allow-protected {path} only with coordinator approval"
                                " (in a shared worktree it may be another Task's change: check git log)")

    # ---- 4. acceptance (shell=True; only reached with intact spec) ----
    if a.rerun_acceptance:
        cmds = data.get("acceptanceCommands") or []
        if not cmds:
            warns.append("no acceptanceCommands in the checklist; nothing rerun")
        for cmd in cmds:
            try:
                r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=a.timeout)
                rc, tail = r.returncode, (r.stdout + r.stderr)[-600:]
            except subprocess.TimeoutExpired:
                rc, tail = 124, f"timeout after {a.timeout}s"
            summary["acceptance"].append({"command": cmd, "exitCode": rc})
            if rc != 0:
                errs.append(f"acceptance command failed (exit {rc}): {cmd}\n    ...{tail.strip()[-400:]}")

    return finish()


if __name__ == "__main__":
    sys.exit(main())
