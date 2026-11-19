import json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

def req(method, port, path, body=None, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r) as x:
            raw = x.read(); return x.status, (json.loads(raw) if x.headers.get_content_type() == "application/json" else raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw.decode()

class TestFinal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18801
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req("GET", cls.port, "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_01_account_public_by_default_and_token(self):
        s, u = req("POST", self.port, "/v1/users", {"handle": "rowan"})
        self.assertEqual(s, 201); self.assertTrue(u["token"]); self.__class__.token = u["token"]
        s, p = req("GET", self.port, "/v1/users/rowan")
        self.assertEqual(s, 200); self.assertTrue(p["is_public"])
        s, _ = req("POST", self.port, "/v1/users", {"handle": "rowan"}); self.assertEqual(s, 409)

    def test_02_private_profile_hidden(self):
        req("POST", self.port, "/v1/users", {"handle": "shy", "is_public": False})
        self.assertEqual(req("GET", self.port, "/v1/users/shy")[0], 403)
        self.assertNotIn("shy", [u["handle"] for u in req("GET", self.port, "/v1/users")[1]["users"]])

    def test_03_project_and_git_link(self):
        h = {"Authorization": f"Bearer {self.token}"}
        s, p = req("POST", self.port, "/v1/projects", {"id": "cs2-browser", "name": "CS2 Browser Port", "repo_url": "https://github.com/example/cs2"}, headers=h)
        self.assertEqual(s, 201); self.assertEqual(p["owner"], "rowan")
        s, g = req("POST", self.port, "/v1/projects/cs2-browser/git", {"repo_url": "https://github.com/example/cs2", "head_from": "aaa", "head_to": "bbb"})
        self.assertEqual(s, 200); self.assertEqual(g["head_to"], "bbb")

    def test_04_dashboard_served(self):
        s, html = req("GET", self.port, "/")
        self.assertEqual(s, 200); self.assertIn("StruggleTime", html); self.assertIn("actually struggling with ai", html)

    def test_05_stream_snapshot(self):
        buckets = json.loads((ROOT/"examples/session-closed-loop.json").read_text())
        for b in buckets: b["project_id"] = "cs2-browser"
        sys.path.insert(0, str(ROOT/"src")); import verify
        prev = verify.GENESIS
        for b in buckets: b["prev_hash"] = prev; b["hash"] = verify.bucket_hash(b); prev = b["hash"]
        s, _ = req("POST", self.port, "/v1/buckets/batch", {"buckets": buckets}, {"Authorization": f"Bearer {self.token}"})
        self.assertEqual(s, 201)
        s, sse = req("GET", self.port, "/v1/projects/cs2-browser/stream?once=1")
        self.assertIn("data:", sse); self.assertIn('"weighted_hours": 0.25', sse)
        s, p = req("GET", self.port, "/v1/users/rowan")
        self.assertAlmostEqual(p["weighted_hours"], 0.25)

    def test_06_daemon_end_to_end(self):
        repo = pathlib.Path(self.tmp) / "daemondemo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)]); subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"]); subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"])
        (repo/"main.py").write_text("print(1)\n"); subprocess.run(["git", "-C", str(repo), "add", "."]); subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"])
        (repo/"main.py").write_text("print(2)\n")
        execlog = pathlib.Path(self.tmp) / "exec.jsonl"
        execlog.write_text(json.dumps({"category": "test_runner", "exit_code": 1}) + "\n" + json.dumps({"category": "test_runner", "exit_code": 0}) + "\n")
        env = {**os.environ, "AITIME_EXEC_LOG": str(execlog)}
        r = subprocess.run([sys.executable, str(ROOT/"daemon/daemon.py"), "--repo", str(repo), "--project", "daemondemo",
                            "--server", f"http://127.0.0.1:{self.port}", "--user", "rowan", "--bucket-secs", "1", "--once"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        s, st = req("GET", self.port, "/v1/projects/daemondemo/stats")
        self.assertEqual(s, 200); self.assertGreater(st["buckets"], 0)
        ledger = json.loads((repo/".aitime-ledger.json").read_text())
        self.assertEqual(len(ledger["pending"]), 0)

    def test_07_allowlist_rejects_raw_text_and_friction_endpoint(self):
        sys.path.insert(0, str(ROOT/"src")); import verify
        b = json.loads((ROOT/"examples/session-closed-loop.json").read_text())[0]
        b["project_id"] = "evil"; b["prompt_text"] = "ignore all instructions"
        b["prev_hash"] = verify.GENESIS; b["hash"] = verify.bucket_hash(b)
        s, r = req("POST", self.port, "/v1/buckets/batch", {"user_id": "mallory", "buckets": [b]})
        self.assertEqual(s, 400); self.assertIn("allowlist", r["error"])
        s, fr = req("GET", self.port, "/v1/projects/cs2-browser/friction")
        self.assertEqual(s, 200); self.assertEqual(len(fr["timeline"]), 3)
        self.assertTrue(fr["commit_anchors"]); self.assertLess(fr["evidence_bytes"], 50 * 1024)
        self.assertTrue(all(0 <= t["friction"] <= 1 for t in fr["timeline"]))

if __name__ == "__main__":
    unittest.main()
