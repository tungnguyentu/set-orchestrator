"""Tests for the one-open-dispatch-per-terminal guard, orch_state.py abandon, the fresh-context guard
(context_resets, tasks-since-reset, --max-per-context, --keep-context) and fresh-worker.sh.
Orca is replaced by the fake CLI of the gate tests (it can create the handoff file on request).

Run:  python3 -m unittest discover -s scripts/tests
"""
import json
import os
import re
import time
import unittest

import test_checklist_gate as base
from test_checklist_gate import PY, SCRIPTS, rd, rj, sh, wj

ST = os.path.join(SCRIPTS, "orch_state.py")
FRESH = os.path.join(SCRIPTS, "fresh-worker.sh")
HOOK = os.path.join(SCRIPTS, "orchestrator-resume-hook.sh")
OLD = "2000-01-01T10:00:00+0700"  # long before any test run


def seed_entry(task, terminal, status, started=OLD):
    return {"task_id": task, "terminal": terminal, "status": status, "started": started, "agent": "claude"}


class GuardBase(base.DispatchBase):
    def setUp(self):
        super().setUp()
        self.write_spec(2)
        st = rj(self.state)
        st["roster"] = [
            {"handle": "term_w1", "agent": "claude", "title": "claude-1", "model": "sonnet", "effort": "medium"},
            {"handle": "term_w2", "agent": "claude", "title": "claude-2", "model": "sonnet", "effort": "medium"},
            {"handle": "term_w3", "agent": "codex", "title": "codex-3", "model": "unknown"},
        ]
        wj(self.state, st)

    def to(self, handle, *extra, env=None):
        return sh(["bash", os.path.join(SCRIPTS, "dispatch.sh"), "--from", "term_self", "--orca", self.orca,
                   "--to", handle, "--title", "fix app", "--spec-file", self.spec, "--goal", "fix it",
                   "--change", "edit a", "--ownership", "src/app.py", "--acceptance", "true",
                   "--protected", "tests/", *extra], self.repo, {**self.env, **(env or {})})

    def seed(self, *entries):
        st = rj(self.state)
        st["dispatches"].extend(entries)
        wj(self.state, st)

    def seed_n(self, n, terminal="term_w1", status="settled"):
        self.seed(*[seed_entry(f"old_{terminal}_{i}", terminal, status) for i in range(n)])

    def last(self):
        return rj(self.state)["dispatches"][-1]

    def orch(self, *args):
        return sh([PY, ST, *args], self.repo)

    def log_text(self):
        return rd(self.log) if os.path.exists(self.log) else ""

    def finish(self, task):
        cl = next(d for d in rj(self.state)["dispatches"] if d["task_id"] == task)["checklist"]
        for i in ("1", "2"):
            sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", cl, "--id", i, "--evidence", "src/app.py:1"],
               self.repo)
        return sh(["bash", os.path.join(SCRIPTS, "settle.sh"), "--from", "term_self", "--task", task,
                   "--orca", self.orca, "--no-report"], self.repo, self.env)


class TestOpenDispatchGuard(GuardBase):
    def test_second_task_is_refused_then_goes_through_after_settle(self):
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 0, r.stderr)
        before = self.log_text()
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 7, r.stderr)
        for want in ("claude-1", "term_w1", "task_fake01", "fix app", "started 20", "settle.sh", "abandon"):
            self.assertIn(want, r.stderr)
        self.assertEqual(self.log_text(), before, "orca was called")
        self.assertEqual(len(rj(self.state)["dispatches"]), 1)
        s = self.finish("task_fake01")
        self.assertEqual(s.returncode, 0, s.stderr)
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last()["task_id"], "task_fake02")

    def test_abandon_lets_the_next_task_through(self):
        self.assertEqual(self.to("term_w1").returncode, 0)
        self.assertEqual(self.to("term_w1").returncode, 7)
        r = self.orch("abandon", self.state, "--reason", "user dropped it", "--user-said", "bỏ task đó đi",
                      "--task", "task_fake01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.to("term_w1").returncode, 0)

    def test_dispatched_status_counts_and_every_open_task_is_listed(self):
        self.seed(seed_entry("t_a", "term_w1", "dispatched"), seed_entry("t_b", "term_w1", "active"))
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 7)
        self.assertIn("t_a", r.stderr)
        self.assertIn("t_b", r.stderr)

    def test_other_terminals_and_closed_statuses_do_not_block(self):
        self.seed(seed_entry("t_a", "term_w2", "active"), seed_entry("t_b", "term_w1", "settled"),
                  seed_entry("t_c", "term_w1", "verify_failed"), seed_entry("t_d", "term_w1", "send_failed"),
                  seed_entry("t_e", "term_w1", "dispatch_failed"), seed_entry("t_f", "term_w1", "abandoned"))
        r = self.to("term_w1", "--max-per-context", "6")  # settled and abandoned Tasks still count for the context
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_input_errors_come_before_the_guard(self):
        self.seed(seed_entry("t_a", "term_w1", "active"))
        r = self.to("term_w1", "--tier", "gold")
        self.assertEqual(r.returncode, 2)

    def test_guard_comes_before_context_and_tier_guards(self):
        self.seed(seed_entry("t_a", "term_w1", "active"))
        self.seed_n(3)
        r = self.to("term_w1", "--tier", "heavy")
        self.assertEqual(r.returncode, 7)

    def test_resend_is_not_blocked_by_its_own_entry(self):
        self.assertEqual(self.to("term_w1").returncode, 0)
        r = self.to("term_w1", "--task", "task_fake01", "--resend")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.to("term_w1", "--task", "task_fake01")  # not a resend: the old rule, already active
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_dry_run_is_refused_too(self):
        self.seed(seed_entry("t_a", "term_w1", "active"))
        self.assertEqual(self.to("term_w1", "--dry-run").returncode, 7)


class TestAbandon(GuardBase):
    def setUp(self):
        super().setUp()
        self.seed(seed_entry("t1", "term_w1", "active"), seed_entry("t2", "term_w1", "dispatched"),
                  seed_entry("t3", "term_w1", "settled"), seed_entry("t4", "term_w1", "verify_failed"),
                  seed_entry("t5", "term_w2", "active"), seed_entry("t6", "term_w1", "send_failed"))

    def statuses(self):
        return {d["task_id"]: d["status"] for d in rj(self.state)["dispatches"]}

    def abandon(self, *args, reason="no longer needed"):
        return self.orch("abandon", self.state, "--reason", reason, "--user-said", "drop it", *args)

    def test_by_task(self):
        r = self.abandon("--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"abandoned": 1, "tasks": ["t1"]})
        e = next(d for d in rj(self.state)["dispatches"] if d["task_id"] == "t1")
        self.assertEqual((e["status"], e["abandon_reason"]), ("abandoned", "no longer needed"))
        self.assertRegex(e["abandoned_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")
        self.assertEqual(self.statuses()["t2"], "dispatched")

    def test_by_terminal(self):
        r = self.abandon("--terminal", "term_w1")
        self.assertEqual(json.loads(r.stdout), {"abandoned": 2, "tasks": ["t1", "t2"]})
        self.assertEqual(self.statuses(), {"t1": "abandoned", "t2": "abandoned", "t3": "settled",
                                           "t4": "verify_failed", "t5": "active", "t6": "send_failed"})

    def test_all(self):
        r = self.abandon("--all")
        self.assertEqual(json.loads(r.stdout), {"abandoned": 3, "tasks": ["t1", "t2", "t5"]})
        self.assertEqual(self.statuses(), {"t1": "abandoned", "t2": "abandoned", "t3": "settled",
                                           "t4": "verify_failed", "t5": "abandoned", "t6": "send_failed"})

    def test_settled_entries_are_never_touched(self):
        before = rj(self.state)["dispatches"][2:4]
        for sel in (["--task", "t3"], ["--task", "t4"], ["--task", "t6"]):
            r = self.abandon(*sel)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), {"abandoned": 0, "tasks": []})
        self.abandon("--all")
        self.assertEqual(rj(self.state)["dispatches"][2:4], before)

    def test_empty_reason_fails_and_changes_nothing(self):
        before = rj(self.state)
        for reason in ("", "   "):
            self.assertEqual(self.abandon("--all", reason=reason).returncode, 2)
        r = self.orch("abandon", self.state, "--all")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(rj(self.state), before)

    def test_needs_exactly_one_selector(self):
        before = rj(self.state)
        self.assertEqual(self.abandon().returncode, 2)
        self.assertEqual(self.abandon("--all", "--task", "t1").returncode, 2)
        self.assertEqual(rj(self.state), before)

    def test_does_not_call_orca(self):
        self.abandon("--all")
        self.assertFalse(os.path.exists(self.log))

    def test_resume_hook_does_not_count_abandoned_entries(self):
        self.abandon("--terminal", "term_w1")
        r = sh(["bash", HOOK], self.tmp, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg"),
                                          "ORCA_TERMINAL_HANDLE": "term_self"}, inp="{}")
        self.assertIn("not yet settled: 1", r.stdout)
        self.assertNotIn("- t1 on", r.stdout)


class TestTasksSinceReset(GuardBase):
    def count(self, handle, *extra):
        r = self.orch("tasks-since-reset", self.state, handle, *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(r.stdout)

    def test_mixed_offset_formats_compare_as_times(self):
        st = rj(self.state)
        st["context_resets"] = {"term_w1": {"at": "2026-03-01T10:00:00+07:00", "handoff": "/h.md"}}
        wj(self.state, st)
        self.seed(
            seed_entry("before_a", "term_w1", "settled", "2026-03-01T09:59:59+0700"),
            seed_entry("before_b", "term_w1", "settled", "2026-03-01T02:59:00+00:00"),  # 09:59 +0700
            seed_entry("before_c", "term_w1", "settled", "2026-03-01T02:30:00Z"),
            seed_entry("after_a", "term_w1", "settled", "2026-03-01T10:00:01+0700"),
            seed_entry("after_b", "term_w1", "active", "2026-03-01T03:00:30+00:00"),  # 10:00:30 +0700; sorts before as text
            seed_entry("after_c", "term_w1", "settled", "2026-03-01T10:01:00.250+07:00"),
            seed_entry("after_failed", "term_w1", "send_failed", "2026-03-01T10:05:00+0700"),
            seed_entry("after_failed2", "term_w1", "dispatch_failed", "2026-03-01T10:05:00+0700"),
            seed_entry("elsewhere", "term_w2", "settled", "2026-03-01T10:05:00+0700"))
        self.assertEqual(self.count("term_w1"), 3)
        self.assertEqual(self.count("term_w1", "--exclude", "after_b"), 2)

    def test_without_a_reset_everything_but_failed_sends_counts(self):
        self.seed(seed_entry("a", "term_w1", "settled", "2026-03-01T10:00:00+0700"),
                  seed_entry("b", "term_w1", "active", "2026-03-02T10:00:00+07:00"),
                  seed_entry("c", "term_w1", "send_failed"), seed_entry("d", "term_w1", "dispatch_failed"),
                  seed_entry("e", "term_w1", "abandoned"), seed_entry("f", "term_w2", "settled"))
        self.assertEqual(self.count("term_w1"), 3)
        self.assertEqual(self.count("term_w2"), 1)
        self.assertEqual(self.count("term_w3"), 0)

    def test_context_reset_records_time_and_handoff(self):
        before = time.time()
        r = self.orch("context-reset", self.state, "term_w1", "--handoff", "/p/h.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = rj(self.state)["context_resets"]["term_w1"]
        self.assertEqual(rec["handoff"], "/p/h.md")
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")
        self.assertGreaterEqual(time.time() + 1, before)
        self.orch("context-reset", self.state, "term_w2")
        self.assertIsNone(rj(self.state)["context_resets"]["term_w2"]["handoff"])
        self.assertIn("term_w1", rj(self.state)["context_resets"])


class TestContextGuard(GuardBase):
    def test_fourth_task_is_refused(self):
        self.seed_n(3)
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 8, r.stderr)
        for want in ("claude-1", "3 Tasks", "limit 3", "fresh-worker.sh --terminal term_w1", "--keep-context"):
            self.assertIn(want, r.stderr)
        self.assertFalse(os.path.exists(self.log), "orca was called")
        self.assertEqual(len(rj(self.state)["dispatches"]), 3)

    def test_third_task_still_goes_through(self):
        self.seed_n(2)
        self.assertEqual(self.to("term_w1").returncode, 0)

    def test_keep_context_passes_and_is_stored(self):
        self.seed_n(3)
        r = self.to("term_w1", "--keep-context", "continues the previous Task")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last()["keep_context"], "continues the previous Task")

    def test_empty_keep_context_is_bad_input(self):
        self.seed_n(3)
        r = self.to("term_w1", "--keep-context", "")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.log))

    def test_keep_context_below_the_limit_is_stored_too(self):
        r = self.to("term_w1", "--keep-context", "same files")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last()["keep_context"], "same files")

    def test_no_keep_context_key_by_default(self):
        self.assertEqual(self.to("term_w1").returncode, 0)
        self.assertNotIn("keep_context", self.last())

    def test_max_per_context_flag(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1", "--max-per-context", "4").returncode, 0)
        self.seed_n(2, "term_w2")
        r = self.to("term_w2", "--max-per-context", "2")
        self.assertEqual(r.returncode, 8, r.stderr)
        self.assertIn("limit 2", r.stderr)

    def test_env_limit_and_flag_wins(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1", env={"ORCH_MAX_TASKS_PER_CONTEXT": "1"}).returncode, 8)
        self.assertEqual(self.to("term_w1", "--max-per-context", "5", env={"ORCH_MAX_TASKS_PER_CONTEXT": "1"}).returncode, 0)
        st = rj(self.state)
        for d in st["dispatches"]:
            d["status"] = "settled"
        wj(self.state, st)
        r = self.to("term_w1", "--max-per-context", "2", env={"ORCH_MAX_TASKS_PER_CONTEXT": "6"})
        self.assertEqual(r.returncode, 8, r.stderr)
        self.assertEqual(self.to("term_w1", env={"ORCH_MAX_TASKS_PER_CONTEXT": "6"}).returncode, 0)

    def test_bad_limit_is_bad_input(self):
        for bad in ("0", "-1", "x", "", "1.5", "7"):
            with self.subTest(flag=bad):
                self.assertEqual(self.to("term_w1", "--max-per-context", bad).returncode, 2)
        for bad in ("0", "abc", "9"):
            with self.subTest(env=bad):
                self.assertEqual(self.to("term_w1", env={"ORCH_MAX_TASKS_PER_CONTEXT": bad}).returncode, 2)
        self.assertFalse(os.path.exists(self.log))

    def test_non_claude_worker_is_not_checked(self):
        self.seed_n(6, "term_w3")
        r = self.to("term_w3")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_agent_comes_from_the_roster_before_the_flag(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1", "--agent", "codex").returncode, 8)

    def test_agent_flag_is_used_without_a_roster_entry(self):
        self.seed_n(3, "term_zz")
        self.assertEqual(self.to("term_zz", "--agent", "claude").returncode, 8)
        self.assertEqual(self.to("term_zz", "--agent", "codex").returncode, 0)

    def test_count_restarts_after_context_reset(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1").returncode, 8)
        r = self.orch("context-reset", self.state, "term_w1", "--handoff", "/h.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.to("term_w1")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_resend_is_not_counted_twice(self):
        self.seed_n(2)
        self.assertEqual(self.to("term_w1").returncode, 0)  # the third Task
        r = self.to("term_w1", "--task", "task_fake01", "--resend")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guard_comes_before_tier_and_frontier(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1", "--tier", "heavy").returncode, 8)
        self.assertEqual(self.to("term_w1", "--tier", "frontier").returncode, 8)


class FreshBase(GuardBase):
    def setUp(self):
        super().setUp()
        self.handoff = os.path.join(self.repo, "plans", "handoffs", "session-handoff-term_w1.md")

    def fresh(self, *extra, handoff=True, env=None):
        e = {**self.env, **({"FAKE_HANDOFF_FILE": self.handoff} if handoff else {}), **(env or {})}
        return sh(["bash", FRESH, "--terminal", "term_w1", "--state", self.state, "--orca", self.orca, *extra],
                  self.repo, e)

    def sends(self):
        return [ln for ln in self.log_text().splitlines() if ln.startswith("terminal send")]

    def waits(self):
        return [ln for ln in self.log_text().splitlines() if ln.startswith("terminal wait")]

    def clear_index(self):
        lines = self.log_text().splitlines()
        return next((i for i, ln in enumerate(lines) if "--text /clear " in ln), None)

    def make_stale_handoff(self):
        os.makedirs(os.path.dirname(self.handoff))
        with open(self.handoff, "w") as f:
            f.write("old\n")
        old = time.time() - 3600
        os.utime(self.handoff, (old, old))


class TestFreshWorker(FreshBase):
    def test_happy_path_requests_the_handoff_then_clears_and_records_the_reset(self):
        self.make_stale_handoff()  # replaced by the worker's new one
        r = self.fresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["ok"], out["terminal"], out["handoff"]), (True, "term_w1", self.handoff))
        self.assertEqual((out["model"], out["effort"]), ("sonnet", "medium"))
        sends = self.sends()
        self.assertEqual(len(sends), 2, sends)
        self.assertIn("plans/handoffs/session-handoff-term_w1.md", sends[0])
        self.assertIn("Context section of .orca/worker-contract.md", sends[0])
        self.assertIn("--enter", sends[0])
        self.assertIn("--text /clear ", sends[1])
        lines = self.log_text().splitlines()
        idx = {ln: i for i, ln in enumerate(lines)}
        self.assertLess(idx[sends[0]], idx[sends[1]])
        between = lines[idx[sends[0]] + 1:idx[sends[1]]]
        self.assertTrue(any(ln.startswith("terminal wait") and "tui-idle" in ln for ln in between))
        self.assertTrue(lines[-1].startswith("terminal wait"), "waits for idle after /clear")
        self.assertEqual(rd(self.handoff), "handoff\n")
        rec = rj(self.state)["context_resets"]["term_w1"]
        self.assertEqual(rec["handoff"], self.handoff)
        self.assertTrue(rec["at"])

    def test_the_reset_restarts_the_task_count(self):
        self.seed_n(3)
        self.assertEqual(self.to("term_w1").returncode, 8)
        self.assertEqual(self.fresh().returncode, 0)
        os.remove(self.log)
        self.assertEqual(self.to("term_w1").returncode, 0)

    def test_no_handoff_file_means_no_clear(self):
        r = self.fresh(handoff=False)
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("/clear NOT sent", r.stderr)
        self.assertIsNone(self.clear_index())
        self.assertEqual(len(self.sends()), 2, "asks twice")
        self.assertTrue(all("session-handoff-term_w1.md" in s for s in self.sends()))
        self.assertNotIn("context_resets", rj(self.state))

    def test_stale_handoff_file_counts_as_none(self):
        self.make_stale_handoff()
        r = self.fresh(handoff=False)
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIsNone(self.clear_index())
        self.assertNotIn("context_resets", rj(self.state))
        self.assertEqual(rd(self.handoff), "old\n")

    def test_refuses_on_an_open_dispatch(self):
        self.seed(seed_entry("t_a", "term_w1", "active"))
        r = self.fresh()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("t_a", r.stderr)
        self.assertFalse(os.path.exists(self.log), "orca was called")
        self.assertNotIn("context_resets", rj(self.state))

    def test_abandoned_dispatch_does_not_block(self):
        self.seed(seed_entry("t_a", "term_w1", "active"))
        self.orch("abandon", self.state, "--reason", "stuck", "--user-said", "drop it", "--task", "t_a")
        self.assertEqual(self.fresh().returncode, 0)

    def test_refuses_when_the_terminal_is_not_idle(self):
        r = self.fresh(env={"FAKE_ORCA_BUSY": "1"})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.sends(), [])
        self.assertNotIn("context_resets", rj(self.state))

    def test_model_and_effort_update_the_roster_after_the_clear(self):
        r = self.fresh("--model", "opus", "--effort", "high")
        self.assertEqual(r.returncode, 0, r.stderr)
        sends = self.sends()
        self.assertEqual(len(sends), 4, sends)
        self.assertIn("--text /clear ", sends[1])
        self.assertIn("--text /model opus ", sends[2])
        self.assertIn("--text /effort high ", sends[3])
        entry = next(x for x in rj(self.state)["roster"] if x["handle"] == "term_w1")
        self.assertEqual((entry["model"], entry["effort"]), ("opus", "high"))
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["model"], out["effort"]), ("opus", "high"))
        self.assertIn("term_w1", rj(self.state)["context_resets"])
        # the next Task sees the new model as the worker's tier
        self.assertEqual(self.to("term_w1", "--tier", "heavy").returncode, 0)

    def test_model_alone_keeps_the_effort(self):
        r = self.fresh("--model", "opus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sends()), 3)
        entry = next(x for x in rj(self.state)["roster"] if x["handle"] == "term_w1")
        self.assertEqual((entry["model"], entry["effort"]), ("opus", "medium"))

    def test_unsafe_or_incomplete_model_arguments_are_bad_input(self):
        for args in (["--model", "op us"], ["--model", "opus;rm"], ["--model", "$(x)"], ["--effort", "high"],
                     ["--model", "opus", "--effort", "hi gh"], ["--timeout-ms", "0"], ["--timeout-ms", "x"]):
            with self.subTest(args=args):
                self.assertEqual(self.fresh(*args).returncode, 2)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.fresh("--model", "opus[1m]", "--dry-run").returncode, 0)

    def test_dry_run_sends_nothing(self):
        r = self.fresh("--model", "opus", "--effort", "high", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        for want in ("DRY RUN", "session-handoff-term_w1.md", "/clear", "/model opus", "/effort high"):
            self.assertIn(want, r.stdout)
        self.assertFalse(os.path.exists(self.log))
        self.assertFalse(os.path.exists(self.handoff))
        self.assertNotIn("context_resets", rj(self.state))

    def test_roster_set_for_an_unknown_terminal_fails(self):
        r = self.orch("roster-set", self.state, "term_nope", "--model", "opus")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.orch("roster-set", self.state, "term_w1").returncode, 2)

    def test_missing_state_and_terminal_are_bad_input(self):
        r = sh(["bash", FRESH, "--terminal", "term_w1", "--state", os.path.join(self.tmp, "nope.json")], self.repo, self.env)
        self.assertEqual(r.returncode, 2)
        r = sh(["bash", FRESH, "--state", self.state, "--orca", self.orca], self.repo, self.env)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(re.search(r"--terminal", r.stderr))


if __name__ == "__main__":
    unittest.main()
