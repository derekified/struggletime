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
        env = {**os.environ, "PORT": str(cls.port), "SERVER_DB": f"{cls.tmp}/t.db", "SLACK_WEBHOOK_URL": ""}
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
        low = html.lower()
        for needle in ("struggletime", "page-landing", "count the loops that fixed the code", "styles.css", "ui.js", "app.js",
                       "fake-stats-preview", "hero-preview", "hero-features", "struggle scoring", "tamper-evident ledger"):
            self.assertIn(needle, low)
        self.assertNotIn("bounty", low)
        self.assertNotIn("hack club's ai", low)
        self.assertNotIn("site-foot", html)
        s, home = req(self.port, "GET", "/home")
        self.assertEqual(s, 200)
        home_l = home.lower()
        for needle in ("friction", "dashboard", "kpi-strip", "rhythm-grid", "filter-strip", "mode-pie", "bento",
                       "terminal", "briefcase", "waveform", "diagram-3", "break-editor", "break-os", "laptop",
                       "fake-stats-badge"):
            self.assertIn(needle, home_l)
        self.assertNotIn("duo-break", home_l)
        self.assertIn('data-auth="required"', home)
        self.assertIn("pendingAuthCode", req(self.port, "GET", "/app.js")[1])
        self.assertIn("clearClientSession", req(self.port, "GET", "/app.js")[1])
        self.assertIn("doLogout", req(self.port, "GET", "/app.js")[1])
        self.assertIn("piechart", req(self.port, "GET", "/ui.js")[1].lower().replace("_", ""))
        s, projects = req(self.port, "GET", "/projects")
        self.assertEqual(s, 200)
        self.assertIn("project-grid", projects)
        self.assertIn("create", projects.lower())
        s, login = req(self.port, "GET", "/login")
        self.assertEqual(s, 200)
        self.assertIn("continue with slack", login.lower())
        s, settings = req(self.port, "GET", "/settings")
        self.assertEqual(s, 200)
        for needle in ("sign in with slack", "connect github", "btn-github-disconnect", "settings-rail", "set-country",
                       "btn-delete-account", "daemon-banner-dismiss",
                       "data-settings-tab=\"docs\"", "st-docs-template", "settings-docs-mount"):
            self.assertIn(needle, settings.lower())
        app_js = req(self.port, "GET", "/app.js")[1]
        self.assertIn("/v1/me/github/disconnect", app_js)
        self.assertIn("pageLoadGen", app_js)
        self.assertIn("TRANSIENT_HTTP", app_js)
        self.assertIn("refreshChain", app_js)
        self.assertIn("load: false", app_js)
        self.assertNotIn('id="side"', settings)
        self.assertNotIn("attach-project", settings)
        s, docs = req(self.port, "GET", "/docs")
        self.assertEqual(s, 200)
        docs_l = docs.lower()
        for needle in ("page-docs", "st-docs-template", "settings-layout", "settings-rail", "settings-panels",
                       "ui-panel", "ui-table", "ui-list",
                       "repository layout", "weighting formula", "contributing", "agpl", "ed25519",
                       "makefile targets", "setup-banner", "kpi-strip", "data-lang"):
            self.assertIn(needle, docs_l)
        self.assertNotIn("copy clone url", docs_l)
        self.assertNotIn("open source · agplv3", docs_l)
        self.assertNotIn("data-docs-copy", docs_l)
        app_js = req(self.port, "GET", "/app.js")[1]
        self.assertIn("mountDocs", app_js)
        self.assertIn("ui-code", app_js)
        self.assertNotIn("data-docs-copy", app_js)
        css = req(self.port, "GET", "/styles.css")[1]
        self.assertIn(".ui-code", css)
        self.assertIn(".ui-table", css)
        self.assertNotIn("docs-hero", css)
        self.assertNotIn(".docs-code", css)
        self.assertNotIn(".docs-table", css)
        self.assertNotIn("color-mix", css)
        s, projects = req(self.port, "GET", "/projects")
        self.assertIn("proj-status", projects)
        self.assertIn("deleted repos", projects.lower())
        s, app = req(self.port, "GET", "/app.js")
        self.assertEqual(s, 200)
        self.assertIn("setInterval", app)
        self.assertIn("toast(", app)
        self.assertIn("icons.hackclub.com", app)
        self.assertIn("showPage", app)
        self.assertIn("KONAMI", app)
        self.assertIn("buildFakeAnalytics", app)
        self.assertIn("mountHeroShot", app)
        self.assertIn('navigate("/")', app)
        s, img = req(self.port, "GET", "/img/fake-stats-preview.svg")
        self.assertEqual(s, 200)
        self.assertIn("dashboard", img.lower())
        self.assertIn("var(--accent", img)
        self.assertIn("var(--bg", img)
        self.assertIn("hs-accent", img)
        self.assertIn("paintTopUser", app)
        self.assertIn("flagEmoji", app)
        self.assertIn("paintSettingsForm", app)
        s, css = req(self.port, "GET", "/styles.css")
        self.assertEqual(s, 200)
        self.assertIn("ui-toast", css)
        self.assertIn("hc-icon", css)
        self.assertIn("nav-stats", css)
        self.assertIn("settings-layout", css)

    def test_analytics_endpoint(self):
        # /v1/me/analytics requires a session or API key
        self.assertEqual(req(self.port, "GET", "/v1/me/analytics?range=7d")[0], 401)
        app = req(self.port, "GET", "/app.js")[1]
        self.assertIn("loadActivePageData", app)
        self.assertIn("fakeStatsMode", app)
        self.assertIn("setFakeStatsMode", app)

    def test_overview_and_submissions_list(self):
        s, ov = req(self.port, "GET", "/v1/overview")
        self.assertEqual(s, 200)
        for k in ("weighted_hours", "users", "projects_count", "pending_reviews", "by_state", "trust_pct", "loop_pct", "weighted_week_hours"):
            self.assertIn(k, ov)
        s, subs = req(self.port, "GET", "/v1/submissions")
        self.assertEqual(s, 401)
        self.assertIn("error", subs)

    def test_overview_reflects_ingest(self):
        buckets = json.loads((ROOT/"examples/session-closed-loop.json").read_text())
        # Handle claim is Slack-session only now; ingest still accepts user_id on batch
        s, _ = req(self.port, "POST", "/v1/buckets/batch", {"user_id": "dash", "buckets": buckets})
        self.assertEqual(s, 201)
        s, ov = req(self.port, "GET", "/v1/overview")
        self.assertEqual(s, 200)
        self.assertGreaterEqual(ov["weighted_hours"], 0.25)
        self.assertGreaterEqual(ov["projects_count"], 1)

if __name__ == "__main__":
    unittest.main()
