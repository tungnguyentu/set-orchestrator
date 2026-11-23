"""Tests for the protected-path baseline (the user's uncommitted work under a protected path does not
fail a Task), abandon needing the user's words, the hard cap on --keep-context, worklog.py and the
work-log part of the resume hook, and the ask hook (orchestrator-ask-hook.sh).

Run:  python3 -m unittest discover -s scripts/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_checklist_gate as base
from test_checklist_gate import PY, SCRIPTS, rd, rj, sh, wj, wr
from test_dispatch_context_guards import GuardBase, HOOK, OLD, ST, seed_entry

sys.path.insert(0, SCRIPTS)
from checklist_spec import spec_hash  # noqa: E402

WORKLOG = os.path.join(SCRIPTS, "worklog.py")
ASK_HOOK = os.path.join(SCRIPTS, "orchestrator-ask-hook.sh")


class TestProtectedBaseline(base.Repo):
    def dirty(self):
        """The user's own uncommitted work under tests/: one modified file, one untracked file."""
        wr(os.path.join(self.repo, "tests", "test_app.py"), "assert True  # user edit\n")
        wr(os.path.join(self.repo, "tests", "notes.txt"), "user notes\n")

    def test_preexisting_dirty_protected_files_pass(self):
        self.dirty()
        h = self.init()
        self.assertEqual(sorted(self.load()["protectedBaseline"]), ["tests/notes.txt", "tests/test_app.py"])
        self.mark_all()
        rc, out, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out["protectedViolations"], [])
        self.assertEqual(out["protectedPreexisting"], ["tests/notes.txt", "tests/test_app.py"])

    def test_changing_a_baseline_file_after_dispatch_fails(self):
        self.dirty()
        h = self.init()
        self.mark_all()
        wr(os.path.join(self.repo, "tests", "notes.txt"), "worker was here\n")
        rc, out, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual([v["path"] for v in out["protectedViolations"]], ["tests/notes.txt"])
        self.assertEqual(out["protectedPreexisting"], ["tests/test_app.py"])

    def test_deleting_a_baseline_file_fails(self):
        self.dirty()
        h = self.init()
        self.mark_all()
        os.remove(os.path.join(self.repo, "tests", "test_app.py"))
        rc, out, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertIn("tests/test_app.py", [v["path"] for v in out["protectedViolations"]])

    def test_new_protected_file_still_fails(self):
        self.dirty()
        h = self.init()
        self.mark_all()
        wr(os.path.join(self.repo, "tests", "added.py"), "x = 1\n")
        rc, out, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual([v["path"] for v in out["protectedViolations"]], ["tests/added.py"])

    def test_worker_cannot_add_or_edit_the_baseline(self):
        h = self.init()  # clean tree: no baseline
        self.assertNotIn("protectedBaseline", self.load())
        wr(os.path.join(self.repo, "tests", "added.py"), "x = 1\n")
        d = self.load()
        d["protectedBaseline"] = {"tests/added.py": "sha256:" + "0" * 64}
        self.save(d, rehash=True)
        self.mark_all()
        rc, out, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertNotEqual(out["specIntegrity"], "ok")

    def test_hash_of_a_checklist_without_baseline_is_unchanged(self):
        d = {"taskId": "t", "baseRef": "abc", "items": [{"id": "1", "step": "s"}], "protectedPaths": ["tests/"],
             "acceptanceCommands": ["true"]}
        self.assertEqual(spec_hash(d), spec_hash(dict(d, protectedBaseline={})))
        self.assertNotEqual(spec_hash(d), spec_hash(dict(d, protectedBaseline={"tests/a": "absent"})))

    def test_files_outside_protected_paths_are_not_recorded(self):
        wr(os.path.join(self.repo, "src", "app.py"), "a = 9\n")
        self.init()
        self.assertNotIn("protectedBaseline", self.load())


class TestAbandonNeedsTheUser(GuardBase):
    def setUp(self):
        super().setUp()
        self.seed(seed_entry("t1", "term_w1", "active"))

    def test_without_user_said_is_refused(self):
        before = rj(self.state)
        for extra in ((), ("--user-said", ""), ("--user-said", "   ")):
            with self.subTest(extra=extra):
                r = self.orch("abandon", self.state, "--reason", "verified: health 200", "--task", "t1", *extra)
                self.assertEqual(r.returncode, 2)
                self.assertIn("settle.sh", r.stderr)
                self.assertIn("--user-said", r.stderr)
        self.assertEqual(rj(self.state), before)

    def test_user_words_are_stored(self):
        r = self.orch("abandon", self.state, "--reason", "user dropped it", "--user-said", "bỏ task này", "--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        e = rj(self.state)["dispatches"][-1]
        self.assertEqual((e["status"], e["abandon_user_said"]), ("abandoned", "bỏ task này"))

    def test_open_dispatch_message_names_the_flag(self):
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 7)
        self.assertIn("--user-said", r.stderr)


class TestKeepContextHardCap(GuardBase):
    def test_keep_context_works_below_the_cap(self):
        self.seed_n(5)
        r = self.to("term_w1", "--keep-context", "continues the previous Task")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_keep_context_stops_at_the_cap(self):
        self.seed_n(6)
        r = self.to("term_w1", "--keep-context", "same deploy routine")
        self.assertEqual(r.returncode, 8, r.stderr)
        for want in ("hard cap 6", "fresh-worker.sh --terminal term_w1", "does not apply"):
            self.assertIn(want, r.stderr)
        self.assertFalse(os.path.exists(self.log))

    def test_limit_cannot_be_raised_past_the_cap(self):
        self.seed_n(6)
        self.assertEqual(self.to("term_w1", "--max-per-context", "50").returncode, 2)
        self.assertEqual(self.to("term_w1", env={"ORCH_MAX_TASKS_PER_CONTEXT": "50"}).returncode, 2)

    def test_other_agents_are_not_capped(self):
        self.seed_n(9, "term_w3")
        self.assertEqual(self.to("term_w3").returncode, 0)


class TestWorklog(GuardBase):
    def setUp(self):
        super().setUp()
        briefs = os.path.join(self.state_dir, "briefs")
        os.makedirs(briefs)
        rows = [("t_old", "Thêm nút import QĐ chi phí", "nút import hiện khi có QĐ giao", "settled", 100),
                ("t_mid", "Deploy to server test", "deploy bản mới", "abandoned", 200),
                ("t_new", "Fix the login page", "sửa trang đăng nhập", "active", 300)]
        for task, change, goal, status, t in rows:
            p = os.path.join(briefs, task + ".txt")
            wr(p, f"Checklist: {self.repo}/.orca/tasks/{task}/checklist.json\n=== TASK ===\nGoal: {goal}\n"
                  f"Change: {change}\nOwnership: src/app.py\n")
            os.utime(p, (t, t))
            e = seed_entry(task, "term_w1", status)
            e.update({"title": "claude-1 · " + task, "base_title": "claude-1", "pane": self.repo})
            if status == "abandoned":
                e["abandon_reason"] = "user dropped it"
            if status == "settled":
                e["verify"] = {"ok": True}
                rep = os.path.join(self.tmp, "report.json")
                wj(rep, {"summary": "nút đã có, 12 test qua"})
                e["report"] = rep
            self.seed(e)
        # a brief of another pane must not leak in
        other = os.path.join(briefs, "t_other.txt")
        wr(other, "Checklist: /somewhere/else/.orca/tasks/t_other/checklist.json\nGoal: nút import\nChange: other pane\n")

    def wl(self, *args):
        return sh([PY, WORKLOG, *args, "--state", self.state], self.tmp)

    def test_recent_lists_newest_first_with_results(self):
        r = self.wl("recent")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("3 of 3 Tasks", out)
        self.assertLess(out.index("t_new"), out.index("t_mid"))
        self.assertLess(out.index("t_mid"), out.index("t_old"))
        for want in ("settled, gate passed", "abandoned (gate not run): user dropped it", "result: nút đã có, 12 test qua",
                     "user asked: nút import hiện khi có QĐ giao"):
            self.assertIn(want, out)
        self.assertNotIn("t_other", out)

    def test_recent_n(self):
        out = self.wl("recent", "-n", "1").stdout
        self.assertIn("t_new", out)
        self.assertNotIn("t_old", out)

    def test_search_ignores_case_and_accents(self):
        for words in (("nut", "IMPORT", "qd", "chi", "phi"), ("Nút", "import", "QĐ", "chi", "phí")):
            with self.subTest(words=words):
                out = self.wl("search", *words).stdout
                first = out.splitlines()[1]
                self.assertIn("t_old", first)
                self.assertIn("matched 5 of 5 words", out)

    def test_search_without_a_match_says_what_to_do(self):
        r = self.wl("search", "kubernetes")
        self.assertEqual(r.returncode, 0)
        self.assertIn("0 of 3 Tasks", r.stdout)
        self.assertIn("git log", r.stdout)

    def test_show_gives_the_paths(self):
        out = self.wl("show", "t_old").stdout
        for want in ("brief:", "report:", "ownership: src/app.py", "worker: claude-1"):
            self.assertIn(want, out)
        self.assertEqual(self.wl("show", "nope").returncode, 2)

    def test_bad_input(self):
        self.assertEqual(self.wl("search").returncode, 2)
        self.assertEqual(self.wl("recent", "-n", "0").returncode, 2)
        self.assertEqual(self.wl("frobnicate").returncode, 2)
        self.assertEqual(sh([PY, WORKLOG, "recent", "--state", os.path.join(self.tmp, "none.json")], self.tmp).returncode, 2)

    def test_writes_nothing(self):
        before = rj(self.state)
        self.wl("recent")
        self.wl("search", "import")
        self.assertEqual(rj(self.state), before)

    def test_resume_hook_prints_rules_search_command_and_recent_tasks(self):
        r = sh(["bash", HOOK], self.tmp, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg"),
                                          "ORCA_TERMINAL_HANDLE": "term_self"}, inp="{}")
        self.assertEqual(r.returncode, 0)
        for want in ("ORCHESTRATOR ROLE ACTIVE", "--user-said", "frozen settle.sh", "project's memory", f"{WORKLOG}' search <words> --state '{self.state}'",
                     "3 of 3 Tasks", "result: nút đã có, 12 test qua"):
            self.assertIn(want, r.stdout)


    # ---- commits, removals, --brief ----
    def commit(self, subject):
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject], cwd=self.repo, check=True, capture_output=True)

    def test_search_lists_matching_commits(self):
        self.commit("fix(rbac): bỏ menu Import/Export khỏi vai Ban")
        out = self.wl("search", "menu", "export").stdout
        self.assertIn("0 of 3 Tasks", out)
        self.assertIn("1 of 2 commits match", out)
        self.assertIn("fix(rbac): bỏ menu Import/Export khỏi vai Ban", out)

    def test_search_lists_what_was_removed_before(self):
        self.commit("fix(rbac): bỏ menu Import/Export khỏi vai Ban")
        self.commit("feat: import mẫu chi phí")
        out = self.wl("search", "import", "mẫu", "-n", "1").stdout
        head, _, tail = out.partition("Removed or hidden before")
        self.assertIn("feat: import mẫu chi phí", head)
        self.assertNotIn("bỏ menu", head)
        self.assertIn("fix(rbac): bỏ menu Import/Export khỏi vai Ban", tail)

    def test_brief_prints_one_line_per_task(self):
        out = self.wl("search", "nút", "import", "--brief").stdout
        self.assertIn("t_old", out)
        self.assertIn(": Thêm nút import QĐ chi phí", out)
        self.assertNotIn("user asked:", out)

    def test_common_words_do_not_outrank_a_rare_word(self):
        briefs = os.path.join(self.state_dir, "briefs")
        for i in range(30):
            wr(os.path.join(briefs, f"t_c{i}.txt"), f"Checklist: {self.repo}/.orca/tasks/t_c{i}/checklist.json\n"
                                                    "Goal: sửa mẫu báo cáo\nChange: mẫu báo cáo tháng\n")
        first = self.wl("search", "mẫu", "báo", "cáo", "đăng", "nhập", "-n", "1").stdout.splitlines()[1]
        self.assertIn("t_new", first)  # "đăng nhập" is in one Task; "mẫu báo cáo" is in 30


class TestAskHook(TestWorklog):
    """orchestrator-ask-hook.sh: the search runs by itself when the user writes and before a question is asked."""

    def hook(self, payload, handle="term_self"):
        r = sh(["bash", ASK_HOOK], self.tmp, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg"),
                                              "ORCA_TERMINAL_HANDLE": handle}, inp=json.dumps(payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def stop(self, message, **extra):
        return self.hook({"hook_event_name": "Stop", "last_assistant_message": message, **extra})

    QUESTION = "Việc A đã xong.\n\nCó thêm lại nút import QĐ chi phí không?\n\nWorker vẫn đang chạy."

    def test_a_question_is_blocked_once_with_the_hits(self):
        d = json.loads(self.stop(self.QUESTION))
        self.assertEqual(d["decision"], "block")
        note = os.path.join(self.state_dir, "ask-hook", "term_self.json.txt")
        self.assertEqual(len(d["reason"].splitlines()), 1)  # the user sees the reason: one line, detail in a file
        self.assertIn(note, d["reason"])
        self.assertIn("do not repeat it", d["reason"])
        detail = rd(note)
        for want in ("t_old", "still exists where", "worklog.py' show", "Do not repeat any of it"):
            self.assertIn(want, detail)
        self.assertNotIn("Việc A đã xong", detail)
        self.assertEqual(self.stop(self.QUESTION), "")  # the same question again goes through

    def test_no_block_without_a_question(self):
        for msg in ("Worker claude-1 vẫn đang sửa 4 test. Tôi sẽ báo kết quả.", "Chạy lệnh `grep -c '?' file.txt` là xong.",
                    "```\nSELECT 1 WHERE a = ?\n```\nXong.", "Ok?"):
            with self.subTest(msg=msg):
                self.assertEqual(self.stop(msg), "")

    def test_no_block_when_stop_hook_active(self):
        self.assertEqual(self.stop(self.QUESTION, stop_hook_active=True), "")

    def test_no_block_when_the_turn_already_searched(self):
        t = os.path.join(self.tmp, "transcript.jsonl")
        rows = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "worklog.py search x"}}]}},
                {"type": "user", "message": {"content": "thêm nút import"}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}]
        wr(t, "\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertIn("block", self.stop(self.QUESTION, transcript_path=t))  # the search was in an earlier turn
        rows.insert(2, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "input": {"command": f"python3 {WORKLOG} search nut import"}}]}})
        wr(t, "\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertEqual(self.stop("Có thêm nút import QĐ chi phí cho Ban không?", transcript_path=t), "")

    def test_user_prompt_gets_the_hits(self):
        out = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "> câu trích dẫn kubernetes\n\nnút import QĐ chi phí đâu rồi"})
        self.assertIn("Work log lookup", out)
        self.assertIn("t_old", out)
        self.assertNotIn("kubernetes", out)

    def test_user_prompt_skips(self):
        for prompt in ("làm", "ok đi", "/set-orchestrator status of the pane", "<task-notification> nút import chi phí",
                       "kubernetes helm terraform"):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.hook({"hook_event_name": "UserPromptSubmit", "prompt": prompt}), "")

    def test_silent_outside_the_orchestrator(self):
        for payload in ({"hook_event_name": "Stop", "last_assistant_message": self.QUESTION},
                        {"hook_event_name": "UserPromptSubmit", "prompt": "nút import QĐ chi phí đâu rồi"}):
            self.assertEqual(self.hook(payload, handle="term_w1"), "")
        self.assertEqual(self.hook({"hook_event_name": "PreToolUse"}), "")
        r = sh(["bash", ASK_HOOK], self.tmp, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg"),
                                              "ORCA_TERMINAL_HANDLE": "term_self"}, inp="not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class TestDispatchRecordsBaseline(GuardBase):
    def test_dispatch_then_settle_with_the_users_dirty_protected_file(self):
        wr(os.path.join(self.repo, "tests", "wip.txt"), "user work in progress\n")
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = json.loads(r.stdout.strip().splitlines()[-1])["taskId"]
        e = next(d for d in rj(self.state)["dispatches"] if d["task_id"] == task)
        self.assertEqual(list(rj(e["checklist"])["protectedBaseline"]), ["tests/wip.txt"])
        r = self.finish(task)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(next(d for d in rj(self.state)["dispatches"] if d["task_id"] == task)["status"], "settled")

    def brief_of(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        task = json.loads(r.stdout.strip().splitlines()[-1])["taskId"]
        return rd(os.path.join(self.state_dir, "briefs", task + ".txt"))

    def test_pstack_brief_names_the_commands_a_worker_may_invoke(self):
        last = self.brief_of(self.to("term_w1", "--family", "pstack")).splitlines()[-1]
        for word in ("/pstack:poteto-mode", "/pstack:architect", "/pstack:how", "/pstack:why", "/pstack:tdd",
                     "/pstack:blast-radius", "/pstack:interrogate", "/poteto-mode"):
            self.assertIn(word, last.split())  # a separate word, no punctuation attached
        self.assertIn("Skill family: `pstack`", rd(os.path.join(self.repo, ".orca", "worker-contract.md")))

    def test_other_families_get_no_such_line(self):
        self.assertNotIn("Method skills", self.brief_of(self.to("term_w2")))


class TestScopeCheck(GuardBase):
    SCOPE = os.path.join(SCRIPTS, "scope-check.py")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def start(self, handle="term_w1"):
        r = self.to(handle)
        self.assertEqual(r.returncode, 0, r.stderr)
        task = json.loads(r.stdout.strip().splitlines()[-1])["taskId"]
        st = rj(self.state)
        for d in st["dispatches"]:
            if d["task_id"] == task:
                d["started"] = OLD  # the commits below are surely after the start
        wj(self.state, st)
        return task

    def scope(self, *args):
        return sh([PY, self.SCOPE, *args, "--state", self.state], self.tmp)

    def test_committed_file_outside_ownership_is_listed(self):
        task = self.start()  # Ownership: src/app.py
        wr(os.path.join(self.repo, "src", "app.py"), "a = 2\n")
        wr(os.path.join(self.repo, "src", "other.py"), "x = 1\n")
        self.git("add", "src/app.py", "src/other.py")
        self.git("commit", "-qm", "change")
        wr(os.path.join(self.repo, "notes.txt"), "the user's uncommitted note\n")
        r = self.scope("--task", task, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual([o["path"] for o in out["outside"]], ["src/other.py"])
        self.assertEqual((out["inside"], out["others"]), (1, []))
        text = self.scope("--task", task).stdout
        self.assertIn("scope: 1 file(s) outside Ownership", text)
        self.assertIn("- src/other.py", text)
        self.assertNotIn("notes.txt", text)

    def test_clean_task_and_no_commit(self):
        task = self.start()
        self.assertIn("scope: 0 file(s) outside Ownership in 0 commit(s)", self.scope("--task", task).stdout)
        wr(os.path.join(self.repo, "src", "app.py"), "a = 3\n")
        self.git("commit", "-qam", "inside only")
        self.assertIn("scope: 0 file(s) outside Ownership in 1 commit(s)", self.scope("--task", task).stdout)

    def test_file_of_a_task_that_ran_at_the_same_time_is_not_mine(self):
        task = self.start()
        other = seed_entry("t_other", "term_w2", "active", started=OLD)
        self.seed(other)
        wr(os.path.join(self.state_dir, "briefs", "t_other.txt"),
           f"Checklist: {self.repo}/.orca/tasks/t_other/checklist.json\nOwnership: docs/ src/lib.py\n")
        os.makedirs(os.path.join(self.repo, "docs"))
        wr(os.path.join(self.repo, "docs", "guide.md"), "x\n")
        wr(os.path.join(self.repo, "README.md"), "x\n")
        self.git("add", "docs/guide.md", "README.md")
        self.git("commit", "-qm", "two files")
        out = json.loads(self.scope("--task", task, "--json").stdout)
        self.assertEqual([o["path"] for o in out["outside"]], ["README.md"])
        self.assertEqual(out["others"], [{"path": "docs/guide.md", "task": "t_other"}])

    def test_bad_input(self):
        self.assertEqual(self.scope().returncode, 2)
        self.assertEqual(self.scope("--task", "nope").returncode, 2)

    def test_contract_points_at_the_project_memory_when_it_exists(self):
        root = os.path.dirname(self.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        mem = os.path.join(self.tmp, "cfg", "projects", "".join(c if c.isalnum() and c.isascii() else "-" for c in root),
                           "memory", "MEMORY.md")
        cmd = ["bash", os.path.join(SCRIPTS, "install-worker-contract.sh"), "--pane", self.repo,
               "--skill", os.path.dirname(SCRIPTS), "--family", "none", "--orca", self.orca]
        contract = os.path.join(self.repo, ".orca", "worker-contract.md")
        self.assertEqual(sh(cmd, self.repo, self.env).returncode, 0)
        self.assertNotIn("MEMORY.md", rd(contract))
        os.makedirs(os.path.dirname(mem))
        wr(mem, "- [Decision](d.md) - a decision\n")
        self.assertEqual(sh(cmd, self.repo, self.env).returncode, 0)
        self.assertIn(mem, rd(contract))


class TestInstallPi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orca-pi-")
        self.env = {"PI_CODING_AGENT_DIR": os.path.join(self.tmp, "agent")}
        self.dest = os.path.join(self.tmp, "agent", "skills", "set-orchestrator")
        self.ext = os.path.join(self.tmp, "agent", "extensions", "set-orchestrator.ts")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self, *args):
        return sh(["bash", os.path.join(SCRIPTS, "install-pi.sh"), *args], self.tmp, self.env)

    def test_link_then_uninstall(self):
        self.assertEqual(self.run_it().returncode, 0)
        self.assertTrue(os.path.islink(self.dest))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "SKILL.md")))
        self.assertTrue(os.path.isfile(self.ext))
        self.assertEqual(self.run_it().returncode, 0)  # a second run replaces its own link
        self.assertEqual(self.run_it("--uninstall").returncode, 0)
        self.assertFalse(os.path.lexists(self.dest))
        self.assertFalse(os.path.exists(self.ext))
        self.assertTrue(os.path.isfile(os.path.join(SCRIPTS, "dispatch.sh")))  # the skill itself is untouched

    def test_copy_then_uninstall(self):
        self.assertEqual(self.run_it("--copy").returncode, 0)
        self.assertFalse(os.path.islink(self.dest))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "scripts", "dispatch.sh")))
        self.assertEqual(self.run_it("--uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.dest))

    def test_a_foreign_folder_is_left_alone(self):
        os.makedirs(self.dest)
        wr(os.path.join(self.dest, "mine.txt"), "x")
        for args in ((), ("--uninstall",)):
            self.assertEqual(self.run_it(*args).returncode, 3)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "mine.txt")))


if __name__ == "__main__":
    unittest.main()
