import json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

def req(method, port, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18799
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req("GET", cls.port, "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def load(self, n): return json.loads((ROOT/"examples"/n).read_text())

    def test_01_health(self):
        self.assertEqual(req("GET", self.port, "/health"), (200, {"ok": True}))

    def test_02_closed_loop_full_weight(self):
        s, b = req("POST", self.port, "/v1/buckets/batch", {"user_id": "u1", "buckets": self.load("session-closed-loop.json")})
        self.assertEqual(s, 201); self.assertAlmostEqual(b["weighted_hours_added"], 0.25)
        s, st = req("GET", self.port, "/v1/projects/proj_1/stats")
        self.assertEqual(st["by_state"], {"STRUGGLE_LOOP": 1, "CLOSED_LOOP": 2})

    def test_03_duplicate_rejected(self):
        s, _ = req("POST", self.port, "/v1/buckets/batch", {"user_id": "u1", "buckets": self.load("session-closed-loop.json")})
        self.assertEqual(s, 409)

    def test_04_tampered_rejected(self):
        sys.path.insert(0, str(ROOT/"src")); import verify
        buckets = self.load("session-closed-loop.json")
        for b in buckets: b["project_id"] = "proj_tamper"
        prev = verify.GENESIS
        for b in buckets:
            b["prev_hash"] = prev; b["hash"] = verify.bucket_hash(b); prev = b["hash"]
        buckets[0]["counts"]["human_edits"] = 999
        s, b = req("POST", self.port, "/v1/buckets/batch", {"user_id": "u2", "buckets": buckets})
        self.assertEqual(s, 422); self.assertIn("tampered", b["error"])

    def test_05_open_loop_discounted_and_flagged(self):
        buckets = self.load("session-open-loop.json")
        for b in buckets: b["project_id"] = "proj_2"
        # re-chain for new project_id
        sys.path.insert(0, str(ROOT/"src")); from verify import bucket_hash, GENESIS
        prev = GENESIS
        for b in buckets:
            b["prev_hash"] = prev; b.pop("hash", None)
            b["hash"] = bucket_hash({**b, "hash": ""}) if False else __import__("verify").bucket_hash(b | {"prev_hash": prev})
            prev = b["hash"]
        s, b = req("POST", self.port, "/v1/buckets/batch", {"user_id": "u1", "buckets": buckets})
        self.assertEqual(s, 201); self.assertLess(b["weighted_hours_added"], 0.05)
        s, st = req("GET", self.port, "/v1/projects/proj_2/stats")
        self.assertTrue(any("ai_human_ratio" in f for f in st["flags"]))

    def test_06_submission_and_deflation(self):
        buckets = self.load("session-closed-loop.json")
        s, sub = req("POST", self.port, "/v1/submissions", {"user_id": "u1", "project_id": "proj_1",
            "from_bucket": min(b["bucket_id"] for b in buckets), "to_bucket": max(b["bucket_id"] for b in buckets),
            "ai_declaration": "AI-assisted, all code reviewed and tested by me"})
        # bucket_id range is lexical, may not cover all; accept 201 or adapt below
        self.assertEqual(s, 201, sub)
        s, r = req("POST", self.port, f"/v1/submissions/{sub['id']}/review",
                   {"reviewer_id": "rev1", "approved_weighted_secs": 0, "reason_code": "BOGUS"})
        self.assertEqual(s, 400)
        s, r = req("POST", self.port, f"/v1/submissions/{sub['id']}/review",
                   {"reviewer_id": "rev1", "approved_weighted_secs": sub["claimed_weighted_secs"], "reason_code": "NONE"})
        self.assertEqual(s, 200); self.assertEqual(r["status"], "approved")

    def test_07_submission_requires_declaration(self):
        s, _ = req("POST", self.port, "/v1/submissions", {"user_id": "u1", "project_id": "proj_1", "from_bucket": "a", "to_bucket": "z"})
        self.assertEqual(s, 400)

    def test_08_unknown_project_404(self):
        self.assertEqual(req("GET", self.port, "/v1/projects/nope/stats")[0], 404)

if __name__ == "__main__":
    unittest.main()
