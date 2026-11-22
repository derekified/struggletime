import hashlib, hmac, json, os, pathlib, secrets, sqlite3, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
from urllib.parse import urlencode
ROOT = pathlib.Path(__file__).parent.parent

def req(port, method, path, body=None, raw=None, headers=None, ctype="application/json", token=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {"Content-Type": ctype, **(headers or {})}
    if token: hdrs["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def seed_user(db, handle="rowan", slack_id="U123"):
    token = secrets.token_urlsafe(16)
    th = hashlib.sha256(token.encode()).hexdigest()
    now = int(time.time())
    c = sqlite3.connect(db)
    c.execute("INSERT OR IGNORE INTO user(handle, token_hash, is_public, created_at) VALUES (?,?,?,?)", (handle, "", 1, now))
    c.execute("INSERT OR REPLACE INTO link(handle, github_login, slack_user_id, github_token, slack_avatar, slack_name) VALUES (?,?,?,?,?,?)",
              (handle, None, slack_id, None, None, None))
    c.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)", (th, handle, slack_id, "", "", now, now + 86400))
    c.execute(
        "INSERT OR IGNORE INTO project(id, owner, name, repo_url, head_from, head_to) VALUES (?,?,?,?,?,?)",
        ("cs2", handle, "CS2", "https://github.com/rowan/cs2-browser", None, None),
    )
    c.commit(); c.close()
    return token

class TestIntegrations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        # Bind an ephemeral port so leftover test servers cannot steal traffic
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.db = f"{cls.tmp}/t.db"
        env = {**os.environ, "STRUGGLETIME_DEV_INGEST": "1", "PORT": str(cls.port), "SERVER_DB": cls.db,
               "GITHUB_WEBHOOK_SECRET": "ghsecret", "SLACK_SIGNING_SECRET": "slsecret",
               "SLACK_WEBHOOK_URL": ""}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = False
        for _ in range(100):
            try:
                if req(cls.port, "GET", "/health")[0] != 200:
                    raise RuntimeError("not up")
                # /health skips DB — poke an authenticated-ish route to create schema
                req(cls.port, "GET", "/v1/projects")
                if pathlib.Path(cls.db).exists():
                    c = sqlite3.connect(cls.db)
                    try:
                        c.execute("SELECT 1 FROM user LIMIT 1")
                        ready = True
                        break
                    finally:
                        c.close()
            except Exception:
                pass
            time.sleep(0.1)
        if not ready:
            raise RuntimeError(f"integrations server failed to init on :{cls.port} db={cls.db}")
        cls.token = seed_user(cls.db)
    @classmethod
    def tearDownClass(cls): cls.proc.terminate()

    def test_github_link_and_parse(self):
        s, r = req(self.port, "POST", "/v1/users/rowan/github", {"github_login": "rowan-17"}, token=self.token)
        self.assertEqual(s, 200); self.assertEqual(r["github_url"], "https://github.com/rowan-17")
        self.assertEqual(req(self.port, "POST", "/v1/users/rowan/github", {"github_login": "bad login!"}, token=self.token)[0], 400)
        self.assertEqual(req(self.port, "POST", "/v1/users/rowan/github", {"github_login": "x"})[0], 401)
        s, g = req(self.port, "GET", "/v1/projects/cs2/github")
        self.assertTrue(g["linked"]); self.assertEqual((g["owner"], g["repo"]), ("rowan", "cs2-browser"))
        s, p = req(self.port, "GET", "/v1/users/rowan"); self.assertEqual(p["github_login"], "rowan-17")

    def test_github_webhook_signature(self):
        payload = json.dumps({"repository": {"full_name": "rowan/cs2-browser"}, "commits": [{"id": "1"}, {"id": "2"}], "after": "abc123"}).encode()
        bad = {"X-Hub-Signature-256": "sha256=nope", "X-GitHub-Event": "push"}
        self.assertEqual(req(self.port, "POST", "/v1/integrations/github/webhook", raw=payload, headers=bad)[0], 401)
        sig = "sha256=" + hmac.new(b"ghsecret", payload, hashlib.sha256).hexdigest()
        s, r = req(self.port, "POST", "/v1/integrations/github/webhook", raw=payload, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"})
        self.assertEqual(s, 202); self.assertEqual(r["commits"], 2); self.assertEqual(r["project_id"], "cs2")
        s, g = req(self.port, "GET", "/v1/projects/cs2/github"); self.assertEqual(len(g["webhook_events"]), 1)

    def test_slack_link_command_and_notify_queue(self):
        form = urlencode({"text": "stats", "user_id": "U123", "user_name": "rowan"}).encode()
        ts = "1700000000"; sig = "v0=" + hmac.new(b"slsecret", f"v0:{ts}:".encode() + form, hashlib.sha256).hexdigest()
        h = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
        s, r = req(self.port, "POST", "/v1/integrations/slack/command", raw=form, headers=h, ctype="application/x-www-form-urlencoded")
        self.assertEqual(s, 200); self.assertIn("@rowan", r["text"]); self.assertIn("weighted h", r["text"])
        bad = dict(h); bad["X-Slack-Signature"] = "v0=nope"
        self.assertEqual(req(self.port, "POST", "/v1/integrations/slack/command", raw=form, headers=bad, ctype="application/x-www-form-urlencoded")[0], 401)
        s, ev = req(self.port, "GET", "/v1/integrations/events"); self.assertEqual(s, 200)

    def test_submission_queues_slack_notification(self):
        sys.path.insert(0, str(ROOT/"src")); import verify, uuid
        c = {"human_edits": 5, "ai_writes": 0, "prompts": 1, "executions": 1, "exec_failures": 0, "commits": 1, "dwell_diff_secs": 0, "idle_secs": 0}
        b = {"v": 1, "bucket_id": str(uuid.uuid4()), "install_id": "i", "project_id": "cs2", "repo_remote_hash": None,
             "bucket_start_wall": 1760000000, "bucket_secs": 300, "mono_delta_ms": 300000, "counts": c,
             "git": {"head_commit": "abc", "lines_added": 9, "lines_deleted": 1, "dirty_files": 1},
             "formula_version": "v0.1.0-alpha", "prev_hash": verify.GENESIS}
        b["hash"] = verify.bucket_hash(b)
        self.assertEqual(req(self.port, "POST", "/v1/buckets/batch", {"user_id": "rowan", "buckets": [b]})[0], 201)
        s, sub = req(self.port, "POST", "/v1/submissions", {"user_id": "rowan", "project_id": "cs2", "from_bucket": b["bucket_id"], "to_bucket": b["bucket_id"], "ai_declaration": "used claude, reviewed all"})
        self.assertEqual(s, 201)
        s, ev = req(self.port, "GET", "/v1/integrations/events")
        texts = [e["text"] for e in ev["events"]]
        self.assertTrue(any("new submission" in t for t in texts), texts)
        self.assertTrue(all(e["delivered"] == 0 for e in ev["events"]))

if __name__ == "__main__":
    unittest.main()
