#!/usr/bin/env python3
"""Zero-hook struggle tomography + reliable active_secs sealing."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))
import verify
import daemon as daemon_mod


class TestTomographyE2E(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="tomo-e2e-"))
        self.home = self.td / "aitime"
        self.home.mkdir()
        self.repo = self.td / "repo"
        self.repo.mkdir()
        for k, v in {
            "AITIME_HOME": str(self.home),
            "STRUGGLETIME_HOME": str(self.home),
            "AITIME_AGENT_EVENTS": str(self.home / "agent_events.jsonl"),
            "AITIME_EXEC_LOG": str(self.home / "exec.jsonl"),
            "STRUGGLETIME_EDITOR": "cursor",
            "STRUGGLETIME_AGENT": "cursor-agent",
        }.items():
            os.environ[k] = v
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.t"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "t"], check=True)
        (self.repo / "a.py").write_text("def f():\n    return 1\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "i"], check=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_tomography_struggle_without_hooks(self):
        """File re-edits + pytest lastfailed + git index pulse → AI_STRUGGLE, active_secs>0."""
        w = daemon_mod.make_watcher(self.repo, "tomo")
        # Same-file thrash (passive)
        for i in range(3):
            (self.repo / "a.py").write_text(f"def f():\n    return {i}\n")
            time.sleep(0.05)
            daemon_mod.poll_repo_files(w, 300)
        self.assertGreaterEqual(w["counts"]["same_file_reedit_count"], 1)
        self.assertGreater(w["counts"].get("active_secs", 0) + (1 if w.get("had_activity") else 0), 0)

        # Build/test cache fail → pass (no shell hooks)
        cache = self.repo / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        lastfailed = cache / "lastfailed"
        lastfailed.write_text('{"a.py::test_f": true}\n')
        daemon_mod.poll_tomography(w)
        self.assertTrue(w.get("open_fail") or w["counts"]["failed_exec_count"] >= 1)
        self.assertGreaterEqual(w["counts"]["build_test_cycles"], 1)
        lastfailed.write_text("{}\n")  # cleared failures
        # size may still be >0; delete to signal pass
        lastfailed.unlink()
        daemon_mod.poll_tomography(w)
        self.assertGreaterEqual(
            w["counts"]["cache_fail_to_pass"] + w["counts"]["test_fail_to_pass_transitions"], 1
        )

        # Git plumbing pulse
        (self.repo / "a.py").write_text("def f():\n    return 99\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "a.py"], check=True)
        daemon_mod.poll_tomography(w)
        self.assertGreaterEqual(w["counts"]["git_plumbing_pulses"], 1)

        b = daemon_mod.seal_bucket(w, 300, "tomo")
        self.assertEqual(verify.validate_allowlist(b), [])
        self.assertGreater(b["counts"]["active_secs"], 0)
        wt, state = verify.weight(b)
        self.assertEqual(wt, 1.0)
        self.assertEqual(state, "AI_STRUGGLE")
        self.assertTrue(verify.is_ai_struggle(b["counts"]))

    def test_live_daemon_once_tomography(self):
        env = {**os.environ}
        # Simulate struggle artifacts before daemon starts, then mutate during --once window
        cache = self.repo / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "lastfailed").write_text('{"x": true}\n')
        (self.repo / "a.py").write_text("def f():\n    return 2\n")

        # Background mutator: during daemon poll, re-edit + clear lastfailed + git add
        mut = self.td / "mutate.sh"
        mut.write_text(
            "#!/bin/sh\n"
            f"sleep 1\n"
            f"echo 'def f():\\n    return 3' > {self.repo}/a.py\n"
            f"sleep 1\n"
            f"echo 'def f():\\n    return 4' > {self.repo}/a.py\n"
            f"rm -f {self.repo}/.pytest_cache/v/cache/lastfailed\n"
            f"git -C {self.repo} add a.py\n"
        )
        mut.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(mut)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        r = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "daemon.py"),
             "--repo", str(self.repo), "--project", "live-tomo",
             "--bucket-secs", "3", "--once", "--server", "", "--token", ""],
            capture_output=True, text=True, env=env, timeout=45,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ledger = json.loads((self.repo / ".aitime-ledger.json").read_text())
        pending = ledger.get("pending") or []
        self.assertTrue(pending, r.stdout)
        b = pending[-1]
        print("\n=== tomography live bucket ===")
        print(json.dumps({
            "active_secs": b["counts"].get("active_secs"),
            "state": verify.weight(b)[1],
            "weight": verify.weight(b)[0],
            "struggle": {k: b["counts"].get(k, 0) for k in (
                "same_file_reedit_count", "atomic_agent_writes", "git_plumbing_pulses",
                "build_test_cycles", "cache_fail_to_pass", "test_fail_to_pass_transitions",
                "failed_exec_count", "ai_writes", "human_edits",
            )},
        }, indent=2))
        self.assertGreater(b["counts"]["active_secs"], 0)
        self.assertEqual(verify.validate_allowlist(b), [])
        insp = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "tracker_inspect.py"), str(self.repo)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(insp.returncode, 0, insp.stdout)
        self.assertIn("struggle=", insp.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
