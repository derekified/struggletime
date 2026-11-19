import json, os, pathlib, subprocess, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
ROOT = pathlib.Path(__file__).parent.parent

class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_j(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/login/oauth/access_token": return self.send_j({"access_token": "ghtest"})
        if self.path == "/api/openid.connect.token": return self.send_j({"access_token": "sltoken"})
        return self.send_j({})
    def do_GET(self):
        if self.path == "/user": return self.send_j({"login": "octocat", "id": 42})
        if self.path == "/api/openid.connect.userInfo": return self.send_j({"sub": "U999", "name": "Slack User"})
        return self.send_j({})

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a): return None

def get(port, path):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}{path}")) as x: return x.status, x.headers.get("Location"), x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.headers.get("Location"), e.read().decode()

def req(port, method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class TestOAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub); cls.stub_port = cls.stub.server_address[1]
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp(); cls.port = 18813
        stub = f"http://127.0.0.1:{cls.stub_port}"
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db",
               "GITHUB_CLIENT_ID": "ghcid", "GITHUB_CLIENT_SECRET": "ghsec", "GITHUB_OAUTH_BASE": stub, "GITHUB_API_BASE": stub,
               "SLACK_CLIENT_ID": "slcid", "SLACK_CLIENT_SECRET": "slsec", "SLACK_OAUTH_BASE": stub,
               "OAUTH_REDIRECT_BASE": f"http://127.0.0.1:{cls.port}"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
        req(cls.port, "POST", "/v1/users", {"handle": "oauthuser"})
    @classmethod
    def tearDownClass(cls): cls.proc.terminate(); cls.stub.shutdown()

    def flow(self, provider):
        s, loc, _ = get(self.port, f"/v1/auth/{provider}/start?handle=oauthuser")
        self.assertEqual(s, 302, loc); self.assertIn("client_id=", loc); self.assertIn("state=", loc)
        state = parse_qs(urlparse(loc).query)["state"][0]
        s, loc2, _ = get(self.port, f"/v1/auth/{provider}/callback?code=testcode&state={state}")
        self.assertEqual(s, 302); self.assertIn(f"oauth={provider}_linked", loc2)
        s, loc3, _ = get(self.port, f"/v1/auth/{provider}/callback?code=testcode&state={state}")
        self.assertIn("bad_state", loc3)

    def test_github_oauth_redirect_flow(self):
        self.flow("github")
        s, p = req(self.port, "POST", "/v1/auth/signin", {"handle": "oauthuser"})
        self.assertEqual(p["github_login"], "octocat")

    def test_slack_oauth_redirect_flow(self):
        self.flow("slack")
        s, p = req(self.port, "POST", "/v1/auth/signin", {"handle": "oauthuser"})
        self.assertEqual(p["slack_user_id"], "U999")

    def test_auth_status_reports_config_without_values(self):
        s, r = req(self.port, "GET", "/v1/auth/status")
        self.assertEqual(s, 200); self.assertTrue(r["github_client_id_set"]); self.assertTrue(r["slack_client_secret_set"])
        self.assertTrue(r["github_callback"].endswith("/v1/auth/github/callback")); self.assertNotIn("ghsec", str(r))

    def test_oauth_requires_claimed_handle(self):
        s, loc, _ = get(self.port, "/v1/auth/github/start?handle=ghost")
        self.assertIn("pick_handle_first", loc)

if __name__ == "__main__":
    unittest.main()
