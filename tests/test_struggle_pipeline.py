#!/usr/bin/env python3
"""Live struggle pipeline: edit→fail→reedit→fail→reedit→pass → AI_STRUGGLE."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))
import verify
import daemon as daemon_mod


class TestStrugglePipeline(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="struggle-pipe-"))
        self.home = self.td / "aitime"
        self.home.mkdir()
        self.repo = self.td / "repo"
        self.repo.mkdir()
        self.agent_log = self.home / "agent_events.jsonl"
        self.exec_log = self.home / "exec.jsonl"
        self._env = {
            "AITIME_HOME": str(self.home),
            "STRUGGLETIME_HOME": str(self.home),
            "AITIME_AGENT_EVENTS": str(self.agent_log),
            "AITIME_EXEC_LOG": str(self.exec_log),
            "STRUGGLETIME_EXEC_LOG": str(self.exec_log),
            "STRUGGLETIME_EDITOR": "cursor",
            "STRUGGLETIME_AGENT": "cursor-agent",
        }
        for k, v in self._env.items():
            os.environ[k] = v
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.t"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "t"], check=True)
        (self.repo / "a.py").write_text("def f():\n    return 1\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "i"], check=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _evt(self, w, **kw):
        now = time.time()
        daemon_mod.apply_event_to_watcher(w, kw, int(now), time.monotonic())

    def test_fail_reedit_pass_other_category(self):
        """category=other pass after fail must count (hooks often omit command name)."""
        w = daemon_mod.make_watcher(self.repo, "pipe")
        self._evt(w, category="editor", event_type="edit", editor="cursor", agent="cursor-agent",
                  files_touched_count=1, exit_code=0)
        self._evt(w, category="other", name="python", exit_code=1, event_type="exec")
        self.assertTrue(w["open_fail"])
        # AI agent re-edit after fail (previously dropped when ai_writes==0)
        self._evt(w, category="editor", event_type="edit", editor="cursor", agent="cursor-agent",
                  files_touched_count=1, exit_code=0)
        self.assertGreater(w["counts"]["same_file_reedit_count"], 0)
        self.assertGreater(w["counts"]["ai_retry_loops"], 0)
        self._evt(w, category="other", name="python", exit_code=1, event_type="exec")
        (self.repo / "a.py").write_text("def f():\n    return 2\n")
        daemon_mod.poll_repo_files(w, 300)
        self._evt(w, category="other", name="python", exit_code=0, event_type="exec")
        self.assertGreaterEqual(w["counts"]["test_fail_to_pass_transitions"], 1)
        self.assertFalse(w["open_fail"])
        b = daemon_mod.seal_bucket(w, 300, "pipe")
        self.assertEqual(verify.validate_allowlist(b), [])
        wt, state = verify.weight(b)
        self.assertEqual(wt, 1.0)
        self.assertEqual(state, "AI_STRUGGLE")
        self.assertTrue(verify.is_ai_struggle(b["counts"]))

    def test_cross_bucket_fail_then_pass(self):
        """Fail in bucket N, pass in bucket N+1 — open_fail must carry."""
        w = daemon_mod.make_watcher(self.repo, "cross")
        self._evt(w, category="test_runner", exit_code=1, event_type="exec")
        (self.repo / "a.py").write_text("def f():\n    return 3\n")
        daemon_mod.poll_repo_files(w, 60)
        b1 = daemon_mod.seal_bucket(w, 60, "cross")
        daemon_mod.reset_window(w, keep_alive=True, after_active=True)
        self.assertTrue(w["open_fail"], "open fail must survive seal")
        self.assertTrue(w["touched_once"], "recent files must carry for re-edit match")
        self._evt(w, category="test_runner", exit_code=0, event_type="exec")
        self.assertGreaterEqual(w["counts"]["test_fail_to_pass_transitions"], 1)
        b2 = daemon_mod.seal_bucket(w, 60, "cross")
        self.assertTrue(verify.is_ai_struggle(b1["counts"]) or verify.is_ai_struggle(b2["counts"]))

    def test_live_daemon_once_and_inspect(self):
        """End-to-end: spool events + daemon --once + inspect + verify."""
        lines = [
            {"ts": int(time.time()), "event_type": "edit", "category": "editor",
             "editor": "cursor", "agent": "cursor-agent", "files_touched_count": 1, "op": "modified"},
            {"ts": int(time.time()), "event_type": "exec", "category": "other",
             "name": "python", "exit_code": 1},
            {"ts": int(time.time()), "event_type": "edit", "category": "editor",
             "editor": "cursor", "agent": "cursor-agent", "files_touched_count": 1, "op": "modified"},
            {"ts": int(time.time()), "event_type": "exec", "category": "other",
             "name": "python", "exit_code": 1},
            {"ts": int(time.time()), "event_type": "edit", "category": "editor",
             "editor": "cursor", "agent": "human", "files_touched_count": 1, "op": "modified"},
            {"ts": int(time.time()), "event_type": "exec", "category": "other",
             "name": "python", "exit_code": 0},
        ]
        with open(self.agent_log, "w") as fh:
            for row in lines:
                fh.write(json.dumps(row) + "\n")
        (self.repo / "a.py").write_text("def f():\n    return 9\n")
        env = {**os.environ, **self._env}
        r = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "daemon.py"),
             "--repo", str(self.repo), "--project", "live",
             "--bucket-secs", "2", "--once", "--server", ""],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ledger = json.loads((self.repo / ".aitime-ledger.json").read_text())
        pending = ledger.get("pending") or []
        self.assertTrue(pending, r.stdout)
        b = pending[0]
        self.assertEqual(verify.validate_allowlist(b), [])
        self.assertEqual(verify.verify_chain(pending), [])
        wt, state = verify.weight(b)
        sc = {k: b["counts"].get(k, 0) for k in (
            "same_file_reedit_count", "ai_retry_loops", "failed_exec_count",
            "test_fail_to_pass_transitions", "agent_turn_churn", "human_recovery_edits",
        )}
        print("\n=== live struggle fields ===")
        print(json.dumps({"state": state, "weight": wt, "struggle": sc,
                          "billable": verify.billable_secs(b),
                          "weighted_secs": verify.billable_secs(b) * wt}, indent=2))
        self.assertGreater(b["counts"].get("failed_exec_count", 0), 0)
        self.assertGreater(b["counts"].get("test_fail_to_pass_transitions", 0), 0)
        self.assertEqual(state, "AI_STRUGGLE")
        self.assertEqual(wt, 1.0)
        insp = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "tracker_inspect.py"), str(self.repo)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(insp.returncode, 0, insp.stdout + insp.stderr)
        self.assertIn("struggle=", insp.stdout)
        self.assertIn("AI_STRUGGLE", insp.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
