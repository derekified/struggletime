import hashlib, json, os, pathlib, secrets, sqlite3, subprocess, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
ROOT = pathlib.Path(__file__).parent.parent

class Stub(BaseHTTPRequestHandler):
    n = 0
    def log_message(self, *a): pass
    def send_j(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/login/oauth/access_token": return self.send_j({"access_token": "ghtest"})
        if self.path == "/api/openid.connect.token": return self.send_j({"access_token": "sltoken"})
        return self.send_j({})
    def do_GET(self):
        if self.path == "/user": return self.send_j({"login": "octocat", "id": 42, "avatar_url": "https://example.com/gh.png"})
        if self.path.startswith("/user/repos"):
            return self.send_j([{"name": "private-app", "full_name": "octocat/private-app", "html_url": "https://github.com/octocat/private-app", "private": True, "description": "secret"}])
        if self.path == "/api/openid.connect.userInfo":
            Stub.n += 1
            return self.send_j({"sub": f"U{Stub.n}", "name": "Slack User", "picture": "https://example.com/sl.png"})
        return self.send_j({})

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a): return None

def get(port, path, headers=None):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})) as x:
            return x.status, x.headers.get("Location"), x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location"), e.read().decode()

def req(port, method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class TestOAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub); cls.stub_port = cls.stub.server_address[1]
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp(); cls.port = 18813; cls.db = f"{cls.tmp}/t.db"
        stub = f"http://127.0.0.1:{cls.stub_port}"
        env = {**os.environ, "STRUGGLETIME_DEV_INGEST": "1", "PORT": str(cls.port), "SERVER_DB": cls.db,
               "GITHUB_CLIENT_ID": "ghcid", "GITHUB_CLIENT_SECRET": "ghsec", "GITHUB_OAUTH_BASE": stub, "GITHUB_API_BASE": stub,
               "SLACK_CLIENT_ID": "slcid", "SLACK_CLIENT_SECRET": "slsec", "SLACK_OAUTH_BASE": stub,
               "OAUTH_REDIRECT_BASE": f"http://127.0.0.1:{cls.port}", "SLACK_WEBHOOK_URL": ""}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)

    @classmethod
    def tearDownClass(cls): cls.proc.terminate(); cls.stub.shutdown()

    def slack_login(self):
        s, loc, _ = get(self.port, "/v1/auth/slack/start")
        self.assertEqual(s, 302, loc); state = parse_qs(urlparse(loc).query)["state"][0]
        s, loc2, _ = get(self.port, f"/v1/auth/slack/callback?code=testcode&state={state}")
        self.assertEqual(s, 302); self.assertIn("auth=", loc2)
        code = parse_qs(urlparse(loc2).query)["auth"][0]
        s, sess = req(self.port, "POST", "/v1/auth/session", {"code": code})
        self.assertEqual(s, 200); self.assertIn("token", sess)
        return sess

    def test_auth_status_reports_config_without_values(self):
        s, r = req(self.port, "GET", "/v1/auth/status")
        self.assertEqual(s, 200); self.assertTrue(r["github_ready"]); self.assertTrue(r["slack_ready"])
        self.assertTrue(r["github_callback"].endswith("/v1/auth/github/callback")); self.assertNotIn("ghsec", str(r))

    def test_handle_login_removed(self):
        self.assertEqual(req(self.port, "POST", "/v1/auth/signin", {"handle": "anyone"})[0], 410)
        self.assertEqual(req(self.port, "POST", "/v1/users", {"handle": "anyone"})[0], 410)

    def test_slack_login_claim_and_avatar(self):
        sess = self.slack_login()
        self.assertTrue(sess["needs_handle"]); self.assertEqual(sess["slack_avatar"], "https://example.com/sl.png")
        token = sess["token"]
        s, me = req(self.port, "GET", "/v1/auth/me", token=token)
        self.assertEqual(s, 200); self.assertTrue(me["needs_handle"])
        s, claimed = req(self.port, "POST", "/v1/auth/claim-handle", {"handle": "oauthuser"}, token=token)
        self.assertEqual(s, 201); self.assertEqual(claimed["handle"], "oauthuser")
        self.assertEqual(claimed["slack_avatar"], "https://example.com/sl.png")
        s, me = req(self.port, "GET", "/v1/auth/me", token=token)
        self.assertEqual(me["handle"], "oauthuser"); self.assertTrue(me["slack_linked"])

    def test_github_link_lists_private_repos(self):
        sess = self.slack_login()
        token = sess["token"]
        if sess.get("needs_handle"):
            self.assertEqual(req(self.port, "POST", "/v1/auth/claim-handle", {"handle": "ghuser"}, token=token)[0], 201)
        s, loc, _ = get(self.port, "/v1/auth/github/start?session=" + token)
        self.assertEqual(s, 302); self.assertIn("scope=", loc); self.assertIn("repo", loc)
        state = parse_qs(urlparse(loc).query)["state"][0]
        s, loc2, _ = get(self.port, f"/v1/auth/github/callback?code=testcode&state={state}")
        self.assertIn("github_linked", loc2)
        s, repos = req(self.port, "GET", "/v1/me/github-repos", token=token)
        self.assertEqual(s, 200); self.assertTrue(any(r["private"] for r in repos["repos"]))
        self.assertEqual(repos["github_login"], "octocat")

    def test_github_disconnect_clears_link(self):
        sess = self.slack_login()
        token = sess["token"]
        if sess.get("needs_handle"):
            self.assertEqual(req(self.port, "POST", "/v1/auth/claim-handle", {"handle": "ghout"}, token=token)[0], 201)
        s, loc, _ = get(self.port, "/v1/auth/github/start?session=" + token)
        state = parse_qs(urlparse(loc).query)["state"][0]
        get(self.port, f"/v1/auth/github/callback?code=testcode&state={state}")
        s, me = req(self.port, "GET", "/v1/auth/me", token=token)
        self.assertEqual(s, 200); self.assertTrue(me.get("github_linked"))
        s, out = req(self.port, "POST", "/v1/me/github/disconnect", token=token)
        self.assertEqual(s, 200, out)
        self.assertTrue(out.get("github_disconnected"))
        self.assertFalse(out.get("github_linked"))
        self.assertIsNone(out.get("github_login"))
        s, repos = req(self.port, "GET", "/v1/me/github-repos", token=token)
        self.assertEqual(s, 400)
        s, again = req(self.port, "POST", "/v1/me/github/disconnect", token=token)
        self.assertEqual(s, 404)

    def test_github_start_requires_slack_session(self):
        s, loc, _ = get(self.port, "/v1/auth/github/start")
        self.assertIn("sign_in_with_slack_first", loc)

    def test_slack_desktop_redirect_to_loopback(self):
        cb = "http://127.0.0.1:39201/callback"
        s, loc, _ = get(self.port, "/v1/auth/slack/start?desktop_redirect=" + cb)
        self.assertEqual(s, 302, loc); state = parse_qs(urlparse(loc).query)["state"][0]
        s, loc2, _ = get(self.port, f"/v1/auth/slack/callback?code=testcode&state={state}")
        self.assertEqual(s, 302)
        self.assertTrue(loc2.startswith(cb), loc2)
        self.assertIn("auth=", loc2)
        code = parse_qs(urlparse(loc2).query)["auth"][0]
        s, sess = req(self.port, "POST", "/v1/auth/session", {"code": code})
        self.assertEqual(s, 200); self.assertIn("token", sess)

    def test_slack_rejects_non_loopback_desktop_redirect(self):
        s, loc, _ = get(self.port, "/v1/auth/slack/start?desktop_redirect=https://evil.example/x")
        self.assertEqual(s, 302, loc); state = parse_qs(urlparse(loc).query)["state"][0]
        s, loc2, _ = get(self.port, f"/v1/auth/slack/callback?code=testcode&state={state}")
        self.assertEqual(s, 302)
        self.assertIn("/login?auth=", loc2)
        self.assertNotIn("evil.example", loc2)

if __name__ == "__main__":
    unittest.main()
