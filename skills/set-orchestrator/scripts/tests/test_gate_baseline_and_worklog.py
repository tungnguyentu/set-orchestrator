"""Tests for the protected-path baseline (the user's uncommitted work under a protected path does not
fail a Task), abandon needing the user's words, the hard cap on --keep-context, worklog.py and the
work-log part of the resume hook.

Run:  python3 -m unittest discover -s scripts/tests
"""
import json
import os
import subprocess
import sys
import unittest

import test_checklist_gate as base
from test_checklist_gate import PY, SCRIPTS, rj, sh, wj, wr
from test_dispatch_context_guards import GuardBase, HOOK, ST, seed_entry

sys.path.insert(0, SCRIPTS)
from checklist_spec import spec_hash  # noqa: E402

WORKLOG = os.path.join(SCRIPTS, "worklog.py")


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
        for want in ("ORCHESTRATOR ROLE ACTIVE", "--user-said", "frozen settle.sh", f"{WORKLOG}' search <words> --state '{self.state}'",
                     "3 of 3 Tasks", "result: nút đã có, 12 test qua"):
            self.assertIn(want, r.stdout)


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


if __name__ == "__main__":
    unittest.main()
