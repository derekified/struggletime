import copy, json, pathlib, subprocess, sys, unittest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
import verify

def bucket(**counts):
    base = {"human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0, "exec_failures": 0,
            "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0}
    base.update(counts)
    return {"v": 1, "bucket_id": "b" * 8, "install_id": "i", "project_id": "p", "repo_remote_hash": None,
            "bucket_start_wall": 0, "bucket_secs": 300, "mono_delta_ms": 300000, "counts": base,
            "git": {"head_commit": "abc123", "lines_added": 1, "lines_deleted": 0, "dirty_files": 1},
            "files": [{"file_id": "0123456789abcdef", "added": 3, "deleted": 1, "bursts": 2}],
            "formula_version": "v0.1.0-alpha", "prev_hash": verify.GENESIS, "hash": "x"}

class TestMetadata(unittest.TestCase):
    def test_allowlist_accepts_metadata_only(self):
        self.assertEqual(verify.validate_allowlist(bucket()), [])

    def test_allowlist_rejects_raw_content(self):
        for field, val in (("prompt_text", "write me code"), ("command", "rm -rf"), ("content", "secret code")):
            b = bucket(); b[field] = val
            self.assertTrue(any("forbidden" in e for e in verify.validate_allowlist(b)), field)
        b = bucket(); b["files"][0]["path"] = "src/secret.py"
        self.assertTrue(any("forbidden" in e for e in verify.validate_allowlist(b)))
        b = bucket(); b["counts"]["keystrokes"] = 42
        self.assertTrue(verify.validate_allowlist(b))

    def test_file_id_anonymized_and_stable(self):
        a = verify.anonymized_file_id("src/main.py", "salt-a")
        self.assertEqual(a, verify.anonymized_file_id("src/main.py", "salt-a"))
        self.assertNotEqual(a, verify.anonymized_file_id("src/main.py", "salt-b"))
        self.assertNotIn("main", a); self.assertEqual(len(a), 16)

    def test_silent_debug_decay_curve(self):
        seq = [bucket() for _ in range(5)]
        ws = verify.weight_sequence(seq)
        self.assertEqual(ws[0], (0.70, "SILENT_DEBUG"))
        self.assertLess(ws[2][0], ws[0][0]); self.assertEqual(ws[4][0], 0.0)
        # steering resets the silence clock
        seq2 = [bucket(), bucket(human_edits=2, executions=1), bucket()]
        self.assertEqual(verify.weight_sequence(seq2)[2], (0.70, "SILENT_DEBUG"))

    def test_friction_rewards_failure_and_followthrough(self):
        hard = verify.friction(bucket(human_edits=6, executions=3, exec_failures=2, prompts=1, commits=1))
        dump = verify.friction(bucket(ai_writes=15, commits=1))
        self.assertGreater(hard, dump); self.assertLessEqual(hard, 1.0)

    def test_inspect_cli(self):
        r = subprocess.run([sys.executable, str(ROOT/"daemon/inspect.py"), str(ROOT/"examples/session-closed-loop.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("payload:", r.stdout); self.assertIn("allowlist: ok", r.stdout)
        self.assertIn("kb", r.stdout)

    def test_metadata_size_budget(self):
        buckets = json.loads((ROOT/"examples/session-closed-loop.json").read_text())
        per_hour_kb = len(json.dumps(buckets).encode()) / 1024 / 0.25
        self.assertLess(per_hour_kb, 50)

if __name__ == "__main__":
    unittest.main()
