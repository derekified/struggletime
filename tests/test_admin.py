import hashlib, json, os, pathlib, sqlite3, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent
STAFF_KEY = "st_test_staff_key_admin"
USER_KEY = "st_test_user_key_admin"

def req(port, method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(r) as x:
            raw = x.read().decode(); ct = x.headers.get_content_type()
            return x.status, (json.loads(raw) if ct == "application/json" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw

class TestAdmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.port = 18841; cls.db = f"{cls.tmp}/t.db"
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": cls.db, "SLACK_WEBHOOK_URL": "",
               "STRUGGLETIME_ADMINS": "boss:superadmin"}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                if req(cls.port, "GET", "/health")[0] == 200: break
            except Exception: time.sleep(0.1)
        now = int(time.time())
        c = sqlite3.connect(cls.db)
        c.execute("UPDATE user SET token_hash=?, trust_level=? WHERE handle=?",
                  (hashlib.sha256(STAFF_KEY.encode()).hexdigest(), "green", "boss"))
        c.execute("INSERT INTO user(handle, token_hash, is_public, created_at, admin_level, trust_level) VALUES (?,?,?,?,?,?)",
                  ("alice", hashlib.sha256(USER_KEY.encode()).hexdigest(), 1, now, "user", "blue"))
        c.execute("INSERT INTO user(handle, token_hash, is_public, created_at, admin_level, trust_level) VALUES (?,?,?,?,?,?)",
                  ("bob", "", 1, now, "user", "blue"))
        c.commit(); c.close()
        cls.staff, cls.user = STAFF_KEY, USER_KEY

    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_guest_submissions_forbidden(self):
        self.assertEqual(req(self.port, "GET", "/v1/submissions")[0], 401)

    def test_user_cannot_list_all_or_admin(self):
        s, r = req(self.port, "GET", "/v1/submissions", token=self.user)
        self.assertEqual(s, 200)
        self.assertEqual(r["submissions"], [])
        self.assertEqual(req(self.port, "GET", "/v1/admin/overview", token=self.user)[0], 403)

    def test_staff_overview_and_trust(self):
        s, ov = req(self.port, "GET", "/v1/admin/overview", token=self.staff)
        self.assertEqual(s, 200)
        self.assertIn("pending_reviews", ov)
        self.assertIn("trust_counts", ov)
        s, ur = req(self.port, "GET", "/v1/admin/users", token=self.staff)
        self.assertEqual(s, 200)
        handles = {u["handle"] for u in ur["users"]}
        self.assertIn("alice", handles)
        s, r = req(self.port, "POST", "/v1/admin/trust",
                   {"handle": "alice", "trust_level": "yellow", "reason": "suspected"}, token=self.staff)
        self.assertEqual(s, 200)
        self.assertEqual(r["trust_level"], "yellow")
        s, logs = req(self.port, "GET", "/v1/admin/trust-audit", token=self.staff)
        self.assertEqual(s, 200)
        self.assertTrue(any(l["handle"] == "alice" and l["new_level"] == "yellow" for l in logs["logs"]))

    def test_role_grant_and_spa_routes(self):
        s, r = req(self.port, "POST", "/v1/admin/role",
                   {"handle": "bob", "admin_level": "reviewer"}, token=self.staff)
        self.assertEqual(s, 200)
        self.assertEqual(r["admin_level"], "reviewer")
        for path in ("/admin", "/review", "/fraud"):
            self.assertEqual(req(self.port, "GET", path)[0], 200)
        js = req(self.port, "GET", "/app.js")[1]
        self.assertIn("canAdmin", js)
        self.assertIn("loadAdminPage", js)
        self.assertIn("STAFF_PAGES", js)
        html = req(self.port, "GET", "/")[1]
        self.assertIn("page-admin", html)
        self.assertIn('data-auth="staff"', html)

if __name__ == "__main__":
    unittest.main()
