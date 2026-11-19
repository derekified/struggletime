import json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src")); import verify

def req(port, method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def chain(project, specs):
    prev, out = verify.GENESIS, []
    for i, counts in enumerate(specs):
        c = {"human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0, "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0, **counts}
        b = {"v": 1, "bucket_id": f"{project}-{i}", "install_id": "i", "project_id": project, "repo_remote_hash": None,
             "bucket_start_wall": 1760000000 + i * 300, "bucket_secs": 300, "mono_delta_ms": 300000, "counts": c,
             "git": {"head_commit": "abc", "lines_added": 5, "lines_deleted": 1, "dirty_files": 1},
             "formula_version": "v0.1.0-alpha", "prev_hash": prev}
        b["hash"] = verify.bucket_hash(b); prev = b["hash"]; out.append(b)
    return out

SILENT = {}
ACTIVE = {"human_edits": 3, "executions": 1}

class TestMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18803
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_01_malformed_json_gets_400_not_dropped_conn(self):
        s, b = req(self.port, "POST", "/v1/buckets/batch", raw=b"{not json")
        self.assertEqual(s, 400)
        self.assertEqual(req(self.port, "GET", "/health")[0], 200)

    def test_02_wrong_types_get_400(self):
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"buckets": "nope"})[0], 400)
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"buckets": [{"bucket_id": "x"}]})[0], 400)
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"buckets": [42]})[0], 400)

    def test_03_silent_decay_continues_across_batches(self):
        s, r = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": chain("silent-a", [SILENT])})
        self.assertAlmostEqual(r["weighted_secs_added"], 210.0)
        s, r = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": chain("silent-b", [SILENT])})
        self.assertAlmostEqual(r["weighted_secs_added"], 210.0)
        # second project reuses chain() ids pattern; continue silent-a with a fresh chained batch
        b2 = chain("silent-a2", [SILENT]); b1hash = chain("silent-a", [SILENT])[0]["hash"]
        b2[0].update({"project_id": "silent-a", "prev_hash": b1hash, "bucket_id": "silent-a-1"})
        b2[0]["hash"] = verify.bucket_hash(b2[0])
        s, r = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": b2})
        self.assertEqual(s, 201, r); self.assertLess(r["weighted_secs_added"], 210.0)

    def test_03b_tampered_replay_is_422_not_409(self):
        buckets = chain("replay-p", [ACTIVE])
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": buckets})[0], 201)
        forged = json.loads(json.dumps(buckets)); forged[0]["counts"]["human_edits"] = 999
        s, r = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": forged})
        self.assertEqual(s, 422); self.assertIn("tampered replay", r["error"])
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": buckets})[0], 409)

    def test_04_steering_resets_silence(self):
        buckets = chain("reset-p", [SILENT, ACTIVE, SILENT])
        s, r = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": buckets})
        self.assertEqual(s, 201); self.assertAlmostEqual(r["weighted_secs_added"], 210 + 300 + 210)

    def test_05_files_over_limit_rejected(self):
        b = chain("manyfiles", [ACTIVE])[0]
        b["files"] = [{"file_id": "0123456789abcdef", "added": 0, "deleted": 0, "bursts": 1}] * 51
        b["hash"] = verify.bucket_hash(b)
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": [b]})[0], 400)

    def test_06_two_projects_independent_chains(self):
        for proj in ("proj-x", "proj-y"):
            s, _ = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": chain(proj, [ACTIVE])})
            self.assertEqual(s, 201)
        self.assertEqual(req(self.port, "GET", "/v1/projects/proj-x/stats")[1]["buckets"], 1)
        self.assertEqual(req(self.port, "GET", "/v1/projects/proj-y/stats")[1]["buckets"], 1)

    def test_07_submission_without_git_link_flagged(self):
        req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u9", "buckets": chain("nogit", [ACTIVE])})
        req(self.port, "POST", "/v1/projects", {"id": "nogit", "name": "nogit"})
        b = chain("nogit", [ACTIVE])[0]
        s, sub = req(self.port, "POST", "/v1/submissions", {"user_id": "u9", "project_id": "nogit", "from_bucket": b["bucket_id"], "to_bucket": b["bucket_id"], "ai_declaration": "pair programmed with claude"})
        self.assertEqual(s, 201); self.assertTrue(any("git_mismatch" in f for f in sub["flags"]), sub)
        self.assertLess(sub["evidence_kb"], 50)

    def test_08_daemon_bucket_has_anon_files_and_no_paths(self):
        repo = pathlib.Path(self.tmp) / "leakcheck"; repo.mkdir()
        for cmd in (["git","init","-q",str(repo)], ["git","-C",str(repo),"config","user.email","t@t.t"], ["git","-C",str(repo),"config","user.name","t"]): subprocess.run(cmd)
        (repo/"secret_project.py").write_text("x=1\n"); subprocess.run(["git","-C",str(repo),"add","."]); subprocess.run(["git","-C",str(repo),"commit","-qm","i"])
        (repo/"secret_project.py").write_text("x=2\n")
        env = {**os.environ, "AITIME_EXEC_LOG": str(pathlib.Path(self.tmp)/"nope.jsonl")}
        r = subprocess.run([sys.executable, str(ROOT/"daemon/daemon.py"), "--repo", str(repo), "--project", "leakcheck", "--bucket-secs", "1", "--once"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = json.loads((repo/".aitime-ledger.json").read_text()); payload = json.dumps(ledger["pending"])
        self.assertNotIn("secret_project", payload); self.assertNotIn(ledger["salt"], payload)
        self.assertTrue(all(len(f["file_id"]) == 16 for b in ledger["pending"] for f in b.get("files", [])))
        r = subprocess.run([sys.executable, str(ROOT/"daemon/tracker_inspect.py"), str(repo)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertNotIn(ledger["salt"], r.stdout)

    def test_09_friction_empty_and_unknown(self):
        self.assertEqual(req(self.port, "GET", "/v1/projects/ghost/friction")[0], 404)
        self.assertEqual(req(self.port, "GET", "/v1/projects/ghost/buckets")[0], 404)

    def test_10_review_deflates_below_claim(self):
        req(self.port, "POST", "/v1/buckets/batch", {"user_id": "u", "buckets": chain("deflate-p", [ACTIVE])})
        b = chain("deflate-p", [ACTIVE])[0]
        s, sub = req(self.port, "POST", "/v1/submissions", {"user_id": "u", "project_id": "deflate-p", "from_bucket": b["bucket_id"], "to_bucket": b["bucket_id"], "ai_declaration": "used ai"})
        s, r = req(self.port, "POST", f"/v1/submissions/{sub['id']}/review", {"reviewer_id": "rev", "approved_weighted_secs": 10, "reason_code": "NO_TEST_ITERATION"})
        self.assertEqual(r["status"], "deflated")

if __name__ == "__main__":
    unittest.main()
