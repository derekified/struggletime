import json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

def req(port, method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x:
            raw = x.read().decode()
            return x.status, (json.loads(raw) if x.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw

class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18805
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_dashboard_html(self):
        s, html = req(self.port, "GET", "/")
        self.assertEqual(s, 200)
        for needle in ("struggletime", "structural friction timeline", "review queue", "builders", "how a minute earns its weight", "setInterval(refresh,2000)"):
            self.assertIn(needle, html.lower() if needle.islower() else html)

    def test_overview_and_submissions_list(self):
        s, ov = req(self.port, "GET", "/v1/overview")
        self.assertEqual(s, 200)
        for k in ("weighted_hours", "users", "projects_count", "pending_reviews", "by_state"): self.assertIn(k, ov)
        s, subs = req(self.port, "GET", "/v1/submissions")
        self.assertEqual(s, 200); self.assertEqual(subs["submissions"], [])

    def test_overview_reflects_ingest(self):
        buckets = json.loads((ROOT/"examples/session-closed-loop.json").read_text())
        req(self.port, "POST", "/v1/users", {"handle": "dash"})
        s, _ = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "dash", "buckets": buckets})
        self.assertEqual(s, 201)
        s, ov = req(self.port, "GET", "/v1/overview")
        self.assertAlmostEqual(ov["weighted_hours"], 0.25); self.assertEqual(ov["projects_count"], 1)

if __name__ == "__main__":
    unittest.main()
