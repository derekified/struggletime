import json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

def req(port, method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x:
            raw = x.read().decode()
            return x.status, (json.loads(raw) if x.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class TestPagesDaemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18811
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_pages_are_separate_routes(self):
        for path, needle in (("/", "page-home"), ("/projects", "page-projects"), ("/builders", "page-builders"),
                             ("/review", "page-review"), ("/integrations", "page-integrations"), ("/settings", "page-settings")):
            s, html = req(self.port, "GET", path)
            self.assertEqual(s, 200, path); self.assertIn(needle, html)
        s, html = req(self.port, "GET", "/settings")
        for needle in ("create account", "sign in", "link github", "link slack", "GITHUB_WEBHOOK_SECRET", "SLACK_SIGNING_SECRET"):
            self.assertIn(needle, html)

    def test_signin_flow(self):
        s, u = req(self.port, "POST", "/v1/users", {"handle": "signinuser"})
        self.assertEqual(s, 201); token = u["token"]
        self.assertEqual(req(self.port, "POST", "/v1/auth/signin", {"handle": "signinuser", "token": "wrong"})[0], 401)
        s, p = req(self.port, "POST", "/v1/auth/signin", {"handle": "signinuser", "token": token})
        self.assertEqual(s, 200); self.assertEqual(p["handle"], "signinuser")
        req(self.port, "POST", "/v1/users/signinuser/github", {"github_login": "octocat"})
        s, p = req(self.port, "POST", "/v1/auth/signin", {"handle": "signinuser", "token": token})
        self.assertEqual(p["github_login"], "octocat")

    def test_helper_lifecycle(self):
        tmp = tempfile.mkdtemp(); repo = pathlib.Path(tmp) / "r"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)])
        (repo / "a.py").write_text("x=1\n")
        env = {**os.environ, "AITIME_HOME": f"{tmp}/aitime"}
        def helper(*args):
            return subprocess.run([sys.executable, str(ROOT/"daemon/helper.py"), *args], capture_output=True, text=True, env=env, timeout=30)
        r = helper("start", "--repo", str(repo), "--user", "t", "--bucket-secs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("started", r.stdout)
        self.assertIn("running", helper("status").stdout)
        self.assertIn("already running", helper("start", "--repo", str(repo)).stdout)
        time.sleep(2.5)
        self.assertIn("started (pid", r.stdout)
        self.assertEqual(helper("stop").returncode, 0)
        self.assertIn("not running", helper("status").stdout)
        logs = helper("logs"); self.assertEqual(logs.returncode, 0)

    def test_installers_parse(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(ROOT/"daemon/install.sh")]).returncode, 0)
        self.assertIn("ScheduledTask", (ROOT/"daemon/install.ps1").read_text())
        self.assertIn("LaunchAgents", (ROOT/"daemon/install.sh").read_text())
        self.assertIn("systemd", (ROOT/"daemon/install.sh").read_text())

if __name__ == "__main__":
    unittest.main()
