import hashlib, json, os, pathlib, secrets, sqlite3, subprocess, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

def req(port, method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers,
    )
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

class TestApiKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18831; cls.db = f"{cls.tmp}/t.db"
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": cls.db, "SLACK_WEBHOOK_URL": "",
               "OAUTH_REDIRECT_BASE": f"http://127.0.0.1:{cls.port}"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server/server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
        now = int(time.time())
        sess = secrets.token_urlsafe(16)
        c = sqlite3.connect(cls.db)
        c.execute("INSERT INTO user(handle, token_hash, is_public, created_at) VALUES (?,?,?,?)",
                  ("akeyuser", "", 1, now))
        c.execute("INSERT INTO link(handle, github_login, slack_user_id, github_token, slack_avatar, slack_name) VALUES (?,?,?,?,?,?)",
                  ("akeyuser", None, "Uakey", None, None, "akey"))
        c.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)",
                  (hashlib.sha256(sess.encode()).hexdigest(), "akeyuser", "Uakey", "", "", now, now + 86400))
        c.commit(); c.close()
        cls.session = sess

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()

    def test_mint_and_use_api_key(self):
        s, st = req(self.port, "GET", "/v1/auth/api-key", token=self.session)
        self.assertEqual(s, 200); self.assertFalse(st["configured"])
        s, minted = req(self.port, "POST", "/v1/auth/api-key", {"confirm": True}, token=self.session)
        self.assertEqual(s, 200); self.assertTrue(minted["api_key"].startswith("st_"))
        key = minted["api_key"]
        s, me = req(self.port, "GET", "/v1/auth/me", token=key)
        self.assertEqual(s, 200); self.assertEqual(me["handle"], "akeyuser"); self.assertEqual(me.get("auth"), "api_key")
        s, st2 = req(self.port, "GET", "/v1/auth/api-key", token=self.session)
        self.assertTrue(st2["configured"]); self.assertTrue(st2["prefix"])
        self.assertEqual(st2.get("api_key"), key)  # persists across refresh
        self.assertTrue(st2.get("has_api_key"))
        self.assertFalse(st2.get("daemon_linked"))  # no buckets yet
        # create project with API key
        s, p = req(self.port, "POST", "/v1/projects",
                   {"id": "akey-proj", "name": "API Key Proj"}, token=key)
        self.assertEqual(s, 201)
        # revoke
        s, _ = req(self.port, "POST", "/v1/auth/api-key/revoke", {}, token=self.session)
        self.assertEqual(s, 200)
        self.assertEqual(req(self.port, "GET", "/v1/auth/me", token=key)[0], 401)
        s, st3 = req(self.port, "GET", "/v1/auth/api-key", token=self.session)
        self.assertFalse(st3["configured"]); self.assertIsNone(st3.get("api_key"))

    def test_settings_page_has_api_key_section(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/settings") as x:
            html = x.read().decode()
            self.assertEqual(x.status, 200)
        self.assertIn("api-key-panel", html)
        self.assertIn("StruggleTime API key", html)
        self.assertIn("btn-api-key-regen", html)
        self.assertIn("settings-daemon", html)
        self.assertIn("install the daemon", html.lower())
        self.assertIn("daemon-banner", html)
        self.assertIn("daemon/install.sh", html)

if __name__ == "__main__":
    unittest.main()
