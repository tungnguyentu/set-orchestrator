#!/usr/bin/env bash
# OPTIONAL, Claude Code orchestrator session only. One script for two hook events:
# UserPromptSubmit and Stop (neither takes a matcher).
#
# A rule that says "search the work log before you ask" is forgotten between compactions. This hook
# runs the search itself and puts the result in front of the model at the two moments it matters.
# It acts only while the Run is open: the state file
# ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/<self-handle>.json exists (self handle:
# $ORCA_TERMINAL_HANDLE, else orca_session_id:$ORCA_AGENT_SESSION_ID). In every other session
# (workers included) it prints nothing and exits 0 before it starts Python. It never fails a session.
#
# UserPromptSubmit: searches the user's prompt (worklog.py search --brief -n 3: Tasks, commits, and
#   what was removed or hidden before) and prints the hits; Claude Code adds them to the context next
#   to the prompt. Lines the user quoted ("> ...") are left out when their own words are 3 or more.
#   Skipped for a prompt that starts with "/" or "<" or has fewer than 3 words.
# Stop: when the last message asks the user a question (a sentence that ends in "?", outside code),
#   searches the text of the question's paragraph and blocks the stop once with {"decision":"block","reason":...}: the
#   reason is one line that names <state dir>/ask-hook/<state file name>.txt (Claude Code shows the reason to the
#   user, so the detail goes into that file): at most 2 hits per group, then what to do (withdraw the question when an entry answers it, confirm that the thing
#   still exists where a user can reach it, else ask again and say what was checked).
#   It does not block when: stop_hook_active is true; the turn already ran worklog.py; or the same
#   question was blocked last time (its hash is in <state dir>/ask-hook/<state file name>.last).
#   So one question costs at most one more turn, and a question asked after a search costs none.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ME="${ORCA_TERMINAL_HANDLE:-${ORCA_AGENT_SESSION_ID:+orca_session_id:$ORCA_AGENT_SESSION_ID}}"
[ -n "$ME" ] || exit 0
STATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/orchestrator/${ME//:/_}.json"
[ -r "$STATE" ] || exit 0
ORCH_HOOK_INPUT="$(cat)" python3 - "$STATE" "$HERE" 2>/dev/null <<'PY' || true
import hashlib, json, os, re, subprocess, sys

state, here = sys.argv[1], sys.argv[2]
try:
    d = json.loads(os.environ.get("ORCH_HOOK_INPUT") or "{}")
    if not isinstance(d, dict):
        sys.exit(0)
    with open(state, encoding="utf-8") as f:
        if not (json.load(f) or {}).get("run_id"):
            sys.exit(0)
except Exception:
    sys.exit(0)


def lookup(text, n):
    """Output of worklog.py search, or '' when no Task, commit or removal matched."""
    try:
        out = subprocess.run([sys.executable, os.path.join(here, "worklog.py"), "search", text[:1500], "-n", str(n),
                              "--brief", "--state", state], capture_output=True, timeout=12).stdout.decode("utf-8", "replace")
    except Exception:
        return ""
    return out.strip() if any(l.startswith("- ") for l in out.splitlines()) else ""


def words(text):
    return re.findall(r"\w+", text)


def ran_worklog(transcript):
    """True when the current turn (everything after the last user prompt) already called worklog.py."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 600_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return False
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except Exception:
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user":
            if isinstance(content, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in content or []):
                return False  # start of the turn
        elif e.get("type") == "assistant":
            for b in content or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and "worklog.py" in json.dumps(b.get("input")):
                    return True
    return False


event = d.get("hook_event_name")
if event == "UserPromptSubmit":
    prompt = str(d.get("prompt") or "").strip()
    own = "\n".join(l for l in prompt.splitlines() if not l.lstrip().startswith(">")).strip()
    if len(set(words(own))) >= 3:
        prompt = own  # the user's own words, without the lines they quoted
    if prompt[:1] in ("/", "<") or len(set(words(prompt))) < 3:
        sys.exit(0)
    hits = lookup(prompt, 3)
    if hits:
        print("Work log lookup for this prompt (automatic, from the pane's Tasks and commits; some lines can be "
              "unrelated). Read it before you say that something exists, write a spec, or ask the user:\n" + hits)
elif event == "Stop":
    if d.get("stop_hook_active"):
        sys.exit(0)
    msg = re.sub(r"```.*?```|`[^`\n]*`", " ", str(d.get("last_assistant_message") or ""), flags=re.S)
    def asks(par):  # a sentence of 3 or more words that ends in a question mark
        return any(s.strip().endswith(("?", "？")) and len(words(s)) >= 3 for s in re.split(r"(?<=[.!?？])\s+|\n+", par))

    # the whole paragraph of each question: "Is this intended?" alone has no words to search with
    asked = [" ".join(par.split()) for par in re.split(r"\n\s*\n", msg) if asks(par)]
    if not asked or ran_worklog(str(d.get("transcript_path") or "")):
        sys.exit(0)
    question = " ".join(asked[-2:])[:900]
    mark = os.path.join(os.path.dirname(state), "ask-hook", os.path.basename(state) + ".last")
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read().strip() == digest:
                sys.exit(0)
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        with open(mark, "w", encoding="utf-8") as f:
            f.write(digest)
    except OSError:
        sys.exit(0)  # no marker, no block: never risk a loop
    hits = lookup(question, 2) or "(no Task, commit or removal matched)"
    detail = (
        "Related Tasks and commits of this pane, found by the words of your question (some can be unrelated):\n"
        f"{hits}\n"
        "If an entry answers the question or shows that the thing was removed or changed, read it "
        f"(python3 '{here}/worklog.py' show <task id> --state '{state}', or git show <commit>) and tell the user "
        "what you found in place of the question. Else confirm that the feature still exists where a user can reach "
        "it (GitNexus or Serena, else git log and grep), then ask again and say in one sentence what you checked.\n")
    # Claude Code shows the reason of a Stop hook to the user, so the reason is one line and the detail is a file
    note = mark[:-len(".last")] + ".txt"
    try:
        with open(note, "w", encoding="utf-8") as f:
            f.write(detail)
        reason = f"Before this question reaches the user, read {note} with the Read tool and do what it says, then answer again."
    except OSError:
        reason = detail
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
PY
exit 0
