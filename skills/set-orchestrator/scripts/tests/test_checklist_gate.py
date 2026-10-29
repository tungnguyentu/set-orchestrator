"""Regression tests for the anti-skip gate (specHash, evidence, dispatch.sh/settle.sh).

Run:  python3 -m pytest scripts/tests -q     (or: python3 -m unittest discover -s scripts/tests -v)
Only needs python3, git and bash; Orca is replaced by a fake CLI.
"""
import json, os, shutil, stat, subprocess, sys, tempfile, unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
from checklist_spec import spec_hash  # noqa: E402

PY = sys.executable


def wr(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rj(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wj(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sh(cmd, cwd, env=None, inp=None):
    e = dict(os.environ)
    e.pop("ORCA_TERMINAL_HANDLE", None)
    e.pop("ORCA_CLI_COMMAND", None)
    e.update(env or {})
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e, input=inp)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orca-gate-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        os.makedirs(os.path.join(self.repo, "tests"))
        for p, body in (("src/app.py", "a = 1\nb = 2\nc = 3\n"), ("tests/test_app.py", "assert True\n")):
            wr(os.path.join(self.repo, p), body)
        for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"],
                  ["git", "add", "."], ["git", "commit", "-qm", "init"]):
            subprocess.run(c, cwd=self.repo, check=True, capture_output=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        self.cl = os.path.join(self.repo, ".orca", "tasks", "task_t1", "checklist.json")

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):  # gate snapshots are read-only
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def init(self, acceptance=("true",), protected=("tests/",), items=("edit src/app.py", "run tests"), extra=()):
        args = [PY, os.path.join(SCRIPTS, "init-checklist.py"), "--task", "task_t1", "--goal", "g"]
        for p in protected:
            args += ["--protected", p]
        for a in acceptance:
            args += ["--acceptance", a]
        for i in items:
            args += ["--item", i]
        r = sh(args + list(extra), self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("specHash=", r.stderr)
        return out["specHash"]

    def load(self):
        return rj(self.cl)

    def save(self, d, rehash=False):
        if rehash:
            d["specHash"] = spec_hash(d)
        wj(self.cl, d)

    def mark_all(self, ev="src/app.py:2"):
        d = self.load()
        for it in d["items"]:
            it["passes"], it["evidence"] = True, ev
        self.save(d)

    def verify(self, *extra):
        r = sh([PY, os.path.join(SCRIPTS, "verify-checklist.py"), "--checklist", self.cl, "--cwd", self.repo,
                "--protected-diff", "--rerun-acceptance", *extra], self.repo)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), r.stderr


class TestSpecHash(Repo):
    def test_init_prints_spec_hash_matching_file(self):
        h = self.init()
        self.assertEqual(h, spec_hash(self.load()))
        self.assertEqual(self.load()["specHash"], h)
        self.assertNotIn("stepsHash", self.load())

    def test_spec_out_written(self):
        out = os.path.join(self.tmp, "state", "specs", "task_t1.json")
        h = self.init(extra=["--spec-out", out])
        spec = rj(out)
        self.assertEqual(spec["specHash"], h)
        self.assertEqual(spec["acceptanceCommands"], ["true"])
        self.mark_all()
        rc, s, _ = self.verify("--spec", out)
        self.assertEqual(rc, 0, s)

    def test_honest_pass(self):
        h = self.init()
        self.mark_all()
        rc, s, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["specIntegrity"], "ok")
        self.assertEqual(s["acceptance"][0]["exitCode"], 0)

    def test_honest_fail_acceptance(self):
        h = self.init(acceptance=("exit 3",))
        self.mark_all()
        rc, s, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual(s["acceptance"][0]["exitCode"], 3)

    def test_clear_acceptance_bypass_fails_with_expect_hash(self):
        h = self.init(acceptance=("exit 3",))
        self.mark_all()
        d = self.load()
        d["acceptanceCommands"] = []
        self.save(d, rehash=True)  # worker recomputes the stored hash too
        rc, s, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual(s["specIntegrity"], "failed")
        self.assertIn("spec tampered", err)

    def test_tampered_acceptance_is_never_executed(self):
        h = self.init()
        self.mark_all()
        d = self.load()
        marker = os.path.join(self.tmp, "PWNED")
        d["acceptanceCommands"] = [f"touch {marker}"]
        self.save(d, rehash=True)
        rc, s, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(marker), "acceptance from a tampered spec ran")
        self.assertEqual(s["acceptance"], [])

    def test_clear_protected_paths_fails(self):
        h = self.init()
        self.mark_all()
        wr(os.path.join(self.repo, "tests/test_app.py"), "pass  # weakened\n")
        rc, s, _ = self.verify("--expect-spec-hash", h)  # honest file: protected violation
        self.assertEqual(rc, 2)
        self.assertTrue(s["protectedViolations"])
        d = self.load()
        d["protectedPaths"] = []
        self.save(d, rehash=True)
        rc, s, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual(s["specIntegrity"], "failed")

    def test_moving_base_ref_fails(self):
        h = self.init()
        self.mark_all()
        d = self.load()
        d["baseRef"] = "HEAD"
        self.save(d, rehash=True)
        rc, s, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)

    def test_delete_hash_field(self):
        h = self.init()
        self.mark_all()
        d = self.load()
        del d["specHash"]
        self.save(d)
        rc, s, err = self.verify()  # no expect: missing field is a failure, not a skip
        self.assertEqual(rc, 2)
        self.assertIn("specHash field missing", err)
        rc, s, err = self.verify("--expect-spec-hash", h)  # expect: computed from contents, field irrelevant
        self.assertEqual(rc, 0, err)
        d = self.load()
        d["items"][0]["step"] = "do nothing"
        self.save(d)
        rc, s, err = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)

    def test_legacy_steps_hash_only_fails(self):
        self.init()
        self.mark_all()
        d = self.load()
        del d["specHash"]
        d["stepsHash"] = "abc"
        self.save(d)
        rc, s, err = self.verify()
        self.assertEqual(rc, 2)
        self.assertIn("legacy stepsHash", err)

    def test_rewrite_step_text(self):
        h = self.init()
        self.mark_all()
        d = self.load()
        d["items"][1]["step"] = "skip tests"
        self.save(d)
        rc, _, err = self.verify()
        self.assertEqual(rc, 2)
        self.assertIn("specHash mismatch", err)
        self.save(d, rehash=True)
        rc, _, _ = self.verify()  # self-attested hash can be forged ...
        self.assertEqual(rc, 0)
        rc, _, err = self.verify("--expect-spec-hash", h)  # ... which is why the coordinator passes H
        self.assertEqual(rc, 2)

    def test_wrong_expect_hash(self):
        self.init()
        self.mark_all()
        rc, _, _ = self.verify("--expect-spec-hash", "0" * 64)
        self.assertEqual(rc, 2)

    def test_empty_evidence(self):
        h = self.init()
        self.mark_all()
        d = self.load()
        d["items"][0]["evidence"] = "   "
        self.save(d)
        rc, s, _ = self.verify("--expect-spec-hash", h)
        self.assertEqual(rc, 2)
        self.assertEqual(s["misses"][0]["why"], "evidence is empty")


class TestEvidence(Repo):
    def setUp(self):
        super().setUp()
        self.h = self.init(protected=())

    def with_ev(self, ev):
        self.mark_all(ev)
        return self.verify("--expect-spec-hash", self.h)

    def test_real_commit_ok(self):
        rc, s, err = self.with_ev(f"commit {self.head[:10]}")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["evidence"][0]["kinds"], ["commit"])

    def test_missing_commit_fails(self):
        rc, s, _ = self.with_ev("commit deadbee1234")
        self.assertEqual(rc, 2)

    def test_file_line_out_of_range_fails(self):
        rc, _, _ = self.with_ev("src/app.py:99")
        self.assertEqual(rc, 2)

    def test_missing_file_fails(self):
        rc, _, _ = self.with_ev("src/nope.py:1")
        self.assertEqual(rc, 2)

    def test_file_range_ok(self):
        rc, _, err = self.with_ev("src/app.py:1-3 changed")
        self.assertEqual(rc, 0, err)

    def test_cmd_exit_ok(self):
        # "true" is the acceptance command, re-run by the gate: verified
        rc, s, err = self.with_ev("true -> exit 0")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["unverifiedItems"], [])

    def test_typed_cmd_claim_is_unverified(self):
        rc, s, err = self.with_ev("pytest -q -> exit 0 (never ran)")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["unverifiedItems"], ["1", "2"])
        self.assertEqual(s["evidence"][0]["kinds"], ["claim"])

    def test_nonzero_exit_on_passed_item_fails(self):
        rc, s, _ = self.with_ev("npm test -> exit 1")
        self.assertEqual(rc, 2)
        self.assertIn("shows a failure", s["errors"][0])

    def test_expected_fail_suffix_ok(self):
        rc, _, err = self.with_ev("pytest tests/test_new.py -> exit 1 (expected fail)")
        self.assertEqual(rc, 0, err)

    def test_host_port_is_not_a_file(self):
        rc, s, err = self.with_ev("server listening on 127.0.0.1:8080 and api.internal:443")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["evidence"][0]["kinds"], ["text"])

    def test_bare_hex_non_commit_is_ignored(self):
        rc, s, err = self.with_ev("docker image 3f2a9b1c7d4e built")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["unverifiedItems"], ["1", "2"])

    def test_bare_hex_real_commit_verified(self):
        rc, s, err = self.with_ev(f"landed in {self.head[:9]}")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["unverifiedItems"], [])

    def test_top_level_code_file_still_checked(self):
        rc, _, _ = self.with_ev("package.json:3")
        self.assertEqual(rc, 2)

    def test_plain_text_is_warning_not_failure(self):
        rc, s, err = self.with_ev("looked fine to me")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["unverifiedItems"], ["1", "2"])
        self.assertTrue(any("unverified" in w for w in s["warnings"]))

    def test_url_is_not_a_file(self):
        rc, _, err = self.with_ev("checked http://localhost:3000 and example.com:443")
        self.assertEqual(rc, 0, err)

    def test_mark_item_run(self):
        r = sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", self.cl, "--id", "1", "--run", "true"], self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.load()["items"][0]["evidence"], "true -> exit 0")
        r = sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", self.cl, "--id", "2", "--run", "exit 4"], self.repo)
        self.assertEqual(r.returncode, 1)
        it = self.load()["items"][1]
        self.assertFalse(it["passes"])
        self.assertEqual(it["evidence"], "exit 4 -> exit 4")
        r = sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", self.cl, "--id", "2", "--run", "exit 4", "--expect-fail"], self.repo)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.load()["items"][1]["passes"])
        self.assertEqual(self.load()["items"][1]["evidence"], "exit 4 -> exit 4 (expected fail)")
        self.assertEqual(spec_hash(self.load()), self.h)


FAKE_ORCA = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_ORCA_LOG"
case "$1 $2" in
  "orchestration task-create") echo '{"ok":true,"result":{"task":{"id":"task_fake01","status":"pending"}}}' ;;
  "orchestration dispatch") echo '{"ok":true,"result":{"dispatch":{"id":"ctx_fake01"}}}' ;;
  *) echo '{"ok":true,"result":{}}' ;;
esac
"""


class TestDispatchSettle(Repo):
    def setUp(self):
        super().setUp()
        self.state_dir = os.path.join(self.tmp, "cfg", "orchestrator")
        os.makedirs(self.state_dir)
        self.state = os.path.join(self.state_dir, "term_self.json")
        wj(self.state, {"run_id": "run_1", "self_handle": "term_self", "pane_path": self.repo, "skill_family": "none",
                   "roster": [{"handle": "term_w1", "agent": "claude", "title": "claude-1"}], "dispatches": []})
        self.orca = os.path.join(self.tmp, "fake-orca")
        wr(self.orca, FAKE_ORCA)
        os.chmod(self.orca, os.stat(self.orca).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "orca.log")
        self.env = {"FAKE_ORCA_LOG": self.log, "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg")}
        self.spec = os.path.join(self.tmp, "spec.md")

    def write_spec(self, n):
        items = "\n".join(f"{i}. step {i} in src/app.py" for i in range(1, n + 1))
        wr(self.spec, f"Target: src/app.py\nDone-when:\n{items}\n\nOut of scope: tests\n")

    def dispatch(self, *extra):
        return sh(["bash", os.path.join(SCRIPTS, "dispatch.sh"), "--from", "term_self", "--orca", self.orca,
                   "--to", "term_w1", "--title", "fix app", "--spec-file", self.spec, "--goal", "fix it",
                   "--change", "edit a", "--ownership", "src/app.py", "--acceptance", "true",
                   "--protected", "tests/", *extra], self.repo, self.env)

    def settle(self):
        return sh(["bash", os.path.join(SCRIPTS, "settle.sh"), "--from", "term_self", "--task", "task_fake01",
                   "--orca", self.orca, "--no-report", "--rename-back"], self.repo, self.env)

    def entry(self):
        return rj(self.state)["dispatches"][0]

    def test_too_many_items_fails_before_orca(self):
        self.write_spec(9)
        r = self.dispatch()
        self.assertEqual(r.returncode, 2)
        self.assertIn("max 8", r.stderr)
        self.assertFalse(os.path.exists(self.log))

    def test_missing_done_when_fails(self):
        wr(self.spec, "Target: x\n")
        r = self.dispatch()
        self.assertEqual(r.returncode, 2)
        self.assertIn("Done-when missing", r.stderr)

    def test_dispatch_then_settle_and_tamper(self):
        self.write_spec(2)
        r = self.dispatch()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        e = self.entry()
        self.assertEqual(e["status"], "active")
        self.assertEqual(e["dispatch_id"], "ctx_fake01")
        self.assertEqual(e["spec_hash"], out["specHash"])
        self.assertTrue(os.path.isfile(e["spec_path"]))
        self.assertTrue(e["spec_path"].startswith(self.state_dir))
        self.assertEqual(out["settle"], os.path.join(self.state_dir, "gate", e["gate_hash"], "settle.sh"))
        self.assertFalse(os.access(out["settle"], os.W_OK))
        brief = rd(out["brief"])
        self.assertIn(f"--expect-spec-hash {e['spec_hash']}", brief)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, ".orca", "worker-contract.md")))
        log = rd(self.log)
        self.assertIn("terminal send", log)
        self.assertIn("claude-1 · fix app", log)
        # re-run of an active Task sends nothing (exit 3); --resend reuses checklist and dispatch
        r = self.dispatch("--task", "task_fake01")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(log.count("terminal send"), rd(self.log).count("terminal send"))
        r = self.dispatch("--task", "task_fake01", "--resend")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log.count("orchestration dispatch"), rd(self.log).count("orchestration dispatch"))
        self.assertEqual(log.count("terminal send") + 1, rd(self.log).count("terminal send"))
        # worker finishes honestly
        cl = e["checklist"]
        for i in ("1", "2"):
            m = sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", cl, "--id", i, "--evidence", "src/app.py:1"], self.repo)
            self.assertEqual(m.returncode, 0, m.stderr)
        s = self.settle()
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(self.entry()["status"], "settled")
        self.assertIn("--title claude-1", rd(self.log))
        # worker tampers with the acceptance and re-stamps the stored hash: settle must fail
        d = rj(cl)
        d["acceptanceCommands"] = []
        d["specHash"] = spec_hash(d)
        wj(cl, d)
        s = self.settle()
        self.assertEqual(s.returncode, 2)
        self.assertEqual(self.entry()["status"], "verify_failed")
        self.assertEqual(self.entry()["verify"]["specIntegrity"], "failed")

    def test_settle_without_gate_hash_fails(self):
        self.write_spec(2)
        self.assertEqual(self.dispatch().returncode, 0)
        st = rj(self.state)
        del st["dispatches"][0]["gate_hash"]
        wj(self.state, st)
        s = self.settle()
        self.assertEqual(s.returncode, 2)
        self.assertIn("gate_hash", s.stdout)

    def test_settle_fails_when_snapshot_tampered(self):
        self.write_spec(2)
        r = self.dispatch()
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.entry()
        for i in ("1", "2"):
            sh([PY, os.path.join(SCRIPTS, "mark-item.py"), "--checklist", e["checklist"], "--id", i, "--evidence", "src/app.py:1"], self.repo)
        gdir = os.path.join(self.state_dir, "gate", e["gate_hash"])
        os.chmod(gdir, 0o755)
        vc = os.path.join(gdir, "verify-checklist.py")
        os.chmod(vc, 0o644)
        wr(vc, "import sys, json\nprint(json.dumps({'ok': True}))\nsys.exit(0)\n")  # worker "fixes" the gate
        s = self.settle()
        self.assertEqual(s.returncode, 2, s.stdout)
        self.assertIn("modified after dispatch", s.stdout)
        self.assertEqual(self.entry()["status"], "verify_failed")

    def test_settle_without_spec_hash_fails(self):
        st = rj(self.state)
        st["dispatches"] = [{"task_id": "task_fake01", "terminal": "term_w1", "status": "active"}]
        wj(self.state, st)
        s = self.settle()
        self.assertEqual(s.returncode, 2)
        self.assertEqual(self.entry()["status"], "verify_failed")

    def test_orca_done_gate_uses_expect_hash(self):
        h = self.init()
        self.mark_all()
        base = ["bash", os.path.join(SCRIPTS, "orca-done.sh"), "--from", "term_w1", "--task", "task_t1",
                "--dispatch", "ctx_1", "--checklist", self.cl, "--cwd", self.repo, "--dry-run", "--orca", self.orca]
        r = sh(base + ["--expect-spec-hash", h], self.repo, self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = sh(base + ["--expect-spec-hash", "f" * 64], self.repo, self.env)
        self.assertEqual(r.returncode, 2)


class TestGateSnapshot(unittest.TestCase):
    """Skill-dir tampering: run against a copy of the scripts so the real skill is untouched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orca-gatesnap-")
        self.src = os.path.join(self.tmp, "scripts")
        shutil.copytree(SCRIPTS, self.src, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        os.makedirs(os.path.join(self.tmp, "orchestrator"))
        self.state = os.path.join(self.tmp, "orchestrator", "self.json")
        wj(self.state, {"dispatches": []})

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def orch(self, *args):
        r = sh([PY, os.path.join(SCRIPTS, "orch_state.py"), *args], self.tmp)
        return r.returncode, json.loads(r.stdout or "{}")

    def test_skill_dir_edit_is_detected(self):
        rc, snap = self.orch("gate-snapshot", self.state, self.src)
        self.assertEqual(rc, 0)
        self.assertEqual(self.orch("gate-check", self.state, snap["hash"])[0], 0)
        with open(os.path.join(self.src, "verify-checklist.py"), "a") as f:
            f.write("\n# worker edit\n")
        rc, res = self.orch("gate-check", self.state, snap["hash"])
        self.assertEqual(rc, 5)
        self.assertIn("verify-checklist.py", res["errors"][0])
        rc, res = self.orch("gate-check", self.state, snap["hash"], "--allow-gate-change")
        self.assertEqual(rc, 0)
        self.assertTrue(res["warnings"])
        # a new snapshot refuses the changed scripts unless the coordinator accepts them
        self.assertEqual(self.orch("gate-snapshot", self.state, self.src)[0], 5)
        rc, snap2 = self.orch("gate-snapshot", self.state, self.src, "--accept-gate-change")
        self.assertEqual(rc, 0)
        self.assertNotEqual(snap2["hash"], snap["hash"])

    def test_snapshot_is_read_only_and_reused(self):
        rc, a = self.orch("gate-snapshot", self.state, self.src)
        rc2, b = self.orch("gate-snapshot", self.state, self.src)
        self.assertEqual((rc, rc2), (0, 0))
        self.assertEqual(a["dir"], b["dir"])
        self.assertFalse(os.access(os.path.join(a["dir"], "verify-checklist.py"), os.W_OK))
        self.assertEqual(rj(self.state)["gate"]["hash"], a["hash"])


if __name__ == "__main__":
    unittest.main()
