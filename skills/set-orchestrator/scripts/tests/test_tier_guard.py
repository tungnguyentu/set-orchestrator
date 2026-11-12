"""Tests for the tier guard and frontier acceptance in dispatch.sh, the model-to-tier table in
orch_state.py, and the orchestrator resume hook. Orca is replaced by the fake CLI of the gate tests.

Run:  python3 -m unittest discover -s scripts/tests
"""
import os
import shutil
import tempfile
import unittest

import test_checklist_gate as base
from test_checklist_gate import PY, SCRIPTS, rj, sh, wj

ST = os.path.join(SCRIPTS, "orch_state.py")
HOOK = os.path.join(SCRIPTS, "orchestrator-resume-hook.sh")


class TestModelTier(unittest.TestCase):
    def tier(self, model):
        r = sh([PY, ST, "model-tier", model], SCRIPTS)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_mapping(self):
        cases = {"haiku": "light", "claude-haiku-4-5": "light", "sonnet": "standard", "Claude Sonnet 5.5": "standard",
                 "opus": "heavy", "claude-opus-4-1": "heavy", "fable": "frontier", "claude-fable-5-1": "frontier",
                 "": "standard", "unknown": "standard", "gpt-5-codex": "standard", "grok-code": "standard"}
        for model, want in cases.items():
            with self.subTest(model=model):
                self.assertEqual(self.tier(model), want)

    def test_missing_argument_is_standard(self):
        r = sh([PY, ST, "model-tier"], SCRIPTS)
        self.assertEqual(r.stdout.strip(), "standard")


class TierBase(base.DispatchBase):
    def setUp(self):
        super().setUp()
        self.write_spec(2)
        st = rj(self.state)
        st["roster"] = [
            {"handle": "term_w1", "agent": "claude", "title": "claude-1", "model": "sonnet", "effort": "medium"},
            {"handle": "term_w2", "agent": "claude", "title": "claude-2", "model": "opus", "effort": "high"},
            {"handle": "term_w3", "agent": "claude", "title": "claude-3", "model": "fable", "effort": "high"},
            {"handle": "term_w4", "agent": "codex", "title": "codex-4", "model": "unknown"},
        ]
        wj(self.state, st)

    def to(self, handle, *extra):
        return sh(["bash", os.path.join(SCRIPTS, "dispatch.sh"), "--from", "term_self", "--orca", self.orca,
                   "--to", handle, "--title", "fix app", "--spec-file", self.spec, "--goal", "fix it",
                   "--change", "edit a", "--ownership", "src/app.py", "--acceptance", "true",
                   "--protected", "tests/", *extra], self.repo, self.env)

    def created_nothing(self):
        self.assertFalse(os.path.exists(self.log), "orca was called")
        self.assertEqual(rj(self.state)["dispatches"], [])


class TestTierGuard(TierBase):
    def test_match_goes_through_and_is_recorded(self):
        r = self.to("term_w1", "--tier", "standard")
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.entry()
        self.assertEqual((e["tier"], e["model"], e["effort"]), ("standard", "sonnet", "medium"))
        self.assertNotIn("tier_override", e)
        self.assertNotIn("WARNING", r.stderr)

    def test_mismatch_exits_5_and_creates_nothing(self):
        r = self.to("term_w1", "--tier", "heavy")
        self.assertEqual(r.returncode, 5, r.stderr)
        for want in ("'heavy'", "claude-1", "sonnet", "tier standard", "claude-2 (opus)"):
            self.assertIn(want, r.stderr)
        self.created_nothing()

    def test_mismatch_with_no_matching_worker_lists_none(self):
        r = self.to("term_w1", "--tier", "light")
        self.assertEqual(r.returncode, 5)
        self.assertIn("tier light: none", r.stderr)
        self.created_nothing()

    def test_override_lets_it_through_and_stores_reason(self):
        r = self.to("term_w1", "--tier", "heavy", "--tier-override", "no opus worker is idle")
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.entry()
        self.assertEqual((e["tier"], e["model"], e["tier_override"]), ("heavy", "sonnet", "no opus worker is idle"))

    def test_override_without_mismatch_is_not_recorded(self):
        r = self.to("term_w1", "--tier", "standard", "--tier-override", "just in case")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("tier_override", self.entry())

    def test_empty_override_is_bad_input(self):
        r = self.to("term_w1", "--tier", "heavy", "--tier-override", "")
        self.assertEqual(r.returncode, 2)
        self.created_nothing()

    def test_bad_tier_value_is_bad_input(self):
        r = self.to("term_w1", "--tier", "gold")
        self.assertEqual(r.returncode, 2)
        self.created_nothing()

    def test_unrecognized_model_counts_as_standard(self):
        self.assertEqual(self.to("term_w4", "--tier", "standard").returncode, 0)
        self.assertEqual(self.entry()["tier"], "standard")

    def test_no_tier_still_works_with_warning(self):
        r = self.to("term_w2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING no --tier", r.stderr)
        e = self.entry()
        self.assertNotIn("tier", e)
        self.assertEqual(e["model"], "opus")

    def test_state_roster_is_the_only_model_source(self):
        # a handle that is not in the roster has no model, so it is standard
        r = self.to("term_zz", "--tier", "heavy")
        self.assertEqual(r.returncode, 5)
        self.assertIn("'unknown' (tier standard)", r.stderr)


class TestFrontierGuard(TierBase):
    QUOTE = "yes, use fable for this one"

    def test_frontier_task_without_acceptance_exits_6(self):
        r = self.to("term_w3", "--tier", "frontier")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.created_nothing()

    def test_override_does_not_bypass_it(self):
        r = self.to("term_w3", "--tier", "frontier", "--tier-override", "because")
        self.assertEqual(r.returncode, 6)
        r = self.to("term_w1", "--tier", "frontier", "--tier-override", "because")
        self.assertEqual(r.returncode, 6)
        self.created_nothing()

    def test_fable_worker_alone_needs_acceptance_even_without_tier(self):
        r = self.to("term_w3")
        self.assertEqual(r.returncode, 6)
        self.created_nothing()

    def test_accepted_flag_goes_through_and_stores_quote(self):
        r = self.to("term_w3", "--tier", "frontier", "--frontier-accepted", self.QUOTE)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = rj(self.state)["frontier_accepted"]["task_fake01"]
        self.assertEqual(rec["quote"], self.QUOTE)
        self.assertTrue(rec["at"])
        e = self.entry()
        self.assertEqual((e["tier"], e["model"], e["frontier_quote"]), ("frontier", "fable", self.QUOTE))

    def test_fable_worker_with_non_frontier_task_needs_only_acceptance(self):
        r = self.to("term_w3", "--tier", "standard", "--frontier-accepted", self.QUOTE)
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.entry()
        self.assertEqual(e["tier"], "standard")
        self.assertNotIn("tier_override", e)
        self.assertEqual(e["frontier_quote"], self.QUOTE)

    def test_frontier_task_on_non_fable_worker_still_needs_override(self):
        r = self.to("term_w2", "--tier", "frontier", "--frontier-accepted", self.QUOTE)
        self.assertEqual(r.returncode, 5, r.stderr)
        r = self.to("term_w2", "--tier", "frontier", "--frontier-accepted", self.QUOTE,
                    "--tier-override", "no fable worker")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_prior_accept_record_with_task_id(self):
        r = sh([PY, ST, "accept-frontier", self.state, "task_fake01", "--quote", self.QUOTE], self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.to("term_w3", "--tier", "frontier", "--task", "task_fake01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.entry()["frontier_quote"], self.QUOTE)

    def test_record_for_another_task_does_not_count(self):
        sh([PY, ST, "accept-frontier", self.state, "task_other", "--quote", self.QUOTE], self.repo)
        r = self.to("term_w3", "--tier", "frontier", "--task", "task_fake01")
        self.assertEqual(r.returncode, 6)

    def test_empty_quote_is_an_error(self):
        for args in (["--quote", ""], ["--quote", "  "], []):
            r = sh([PY, ST, "accept-frontier", self.state, "task_fake01", *args], self.repo)
            self.assertEqual(r.returncode, 2, args)
        self.assertNotIn("frontier_accepted", rj(self.state))
        r = self.to("term_w3", "--tier", "frontier", "--frontier-accepted", "")
        self.assertEqual(r.returncode, 2)
        self.created_nothing()

    def test_resend_keeps_tier_fields(self):
        self.assertEqual(self.to("term_w3", "--tier", "frontier", "--frontier-accepted", self.QUOTE).returncode, 0)
        r = self.to("term_w3", "--task", "task_fake01", "--resend")
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.entry()
        self.assertEqual((e["tier"], e["frontier_quote"]), ("frontier", self.QUOTE))

    def test_settle_keeps_tier_data_in_the_entry(self):
        self.assertEqual(self.to("term_w1", "--tier", "heavy", "--tier-override", "no opus").returncode, 0)
        e = self.entry()
        for i in ("1", "2"):
            sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", e["checklist"], "--id", i,
                "--evidence", "src/app.py:1"], self.repo)
        self.assertEqual(self.settle().returncode, 0)
        e = self.entry()
        self.assertEqual((e["status"], e["tier"], e["model"], e["tier_override"]),
                         ("settled", "heavy", "sonnet", "no opus"))


class TestResumeHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orca-hook-")
        self.cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(os.path.join(self.cfg, "orchestrator"))
        self.hook_env = {"CLAUDE_CONFIG_DIR": self.cfg, "ORCA_TERMINAL_HANDLE": "term_self",
                         "ORCA_AGENT_SESSION_ID": ""}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self, name="term_self.json", **kw):
        path = os.path.join(self.cfg, "orchestrator", name)
        data = {"run_id": "run_42", "self_handle": "term_self", "pane_path": "/work/pane", "secret": "do-not-print",
                "dispatches": [{"task_id": "t1", "status": "active"}, {"task_id": "t2", "status": "settled"},
                               {"task_id": "t3", "status": "dispatched"}]}
        data.update(kw)
        wj(path, data)
        return path

    def run_hook(self, env=None):
        r = sh(["bash", HOOK], self.tmp, env if env is not None else self.hook_env, inp='{"source":"compact"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_open_run_prints_reminder(self):
        path = self.state()
        out = self.run_hook()
        for want in ("ORCHESTRATOR", "run_42", "/work/pane", path, "not yet settled: 2", "/set-orchestrator status",
                     "Do not edit code"):
            self.assertIn(want, out)
        self.assertLess(len(out.splitlines()[0]), 600)
        self.assertNotIn("do-not-print", out)
        self.assertIn("- t1 on", out)
        self.assertNotIn("- t2 on", out)  # settled

    def test_reminder_lists_the_workers(self):
        self.state(roster=[{"handle": "term_w1", "title": "claude-1", "agent": "claude", "model": "sonnet",
                            "effort": "unknown"},
                           {"handle": "term_w2", "title": "agy-2", "agent": "antigravity", "model": "unknown"}],
                   dispatches=[{"task_id": "t9", "status": "active", "base_title": "claude-1", "tier": "standard"}])
        out = self.run_hook()
        for want in ("Your workers in this pane (2)", "- claude-1 (claude, sonnet): term_w1",
                     "- agy-2 (antigravity, model unknown): term_w2", "- t9 on claude-1 (tier standard)"):
            self.assertIn(want, out)

    def test_no_roster_and_no_open_dispatch_is_one_line(self):
        self.state(dispatches=[{"task_id": "t2", "status": "settled"}])
        self.assertEqual(len(self.run_hook().strip().splitlines()), 1)

    def test_chat_session_address_maps_to_file_name(self):
        self.state("orca_session_id_abc.json")
        out = self.run_hook({**self.hook_env, "ORCA_TERMINAL_HANDLE": "", "ORCA_AGENT_SESSION_ID": "abc"})
        self.assertIn("run_42", out)

    def test_no_state_file_prints_nothing(self):
        self.assertEqual(self.run_hook(), "")

    def test_closed_run_prints_nothing(self):
        path = self.state()
        os.remove(path)  # `off` deletes the state file
        self.assertEqual(self.run_hook(), "")

    def test_no_handle_prints_nothing(self):
        self.state()
        self.assertEqual(self.run_hook({**self.hook_env, "ORCA_TERMINAL_HANDLE": ""}), "")

    def test_garbage_state_prints_nothing_and_exits_0(self):
        path = self.state()
        with open(path, "w") as f:
            f.write("{not json")
        self.assertEqual(self.run_hook(), "")

    def test_state_without_run_id_prints_nothing(self):
        self.state(run_id="")
        self.assertEqual(self.run_hook(), "")

    def test_long_and_multiline_values_stay_short(self):
        self.state(pane_path="/p\nINJECT " + "x" * 5000)
        out = self.run_hook()
        self.assertLess(len(out), 1200)
        self.assertFalse([ln for ln in out.splitlines() if ln.startswith("INJECT")])


if __name__ == "__main__":
    unittest.main()
