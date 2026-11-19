import hashlib, hmac, json, os, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).parent.parent

class TestDotenv(unittest.TestCase):
    def test_server_loads_dotenv_from_cwd(self):
        tmp = tempfile.mkdtemp(); port = 18809
        pathlib.Path(tmp, ".env").write_text(f"PORT={port}\nSERVER_DB={tmp}/from-env.db\nGITHUB_WEBHOOK_SECRET=envsecret\n# comment\n")
        env = {k: v for k, v in os.environ.items() if k not in ("PORT", "SERVER_DB", "GITHUB_WEBHOOK_SECRET")}
        proc = subprocess.Popen([sys.executable, str(ROOT/"server/server.py")], cwd=tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            body = b""
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as x: body = x.read(); break
                except Exception: time.sleep(0.1)
            self.assertEqual(json.loads(body), {"ok": True})
            self.assertTrue(pathlib.Path(tmp, "from-env.db").exists())
            payload = json.dumps({"repository": {"full_name": "x/y"}, "commits": [], "after": "abc"}).encode()
            sig = "sha256=" + hmac.new(b"envsecret", payload, hashlib.sha256).hexdigest()
            r = urllib.request.Request(f"http://127.0.0.1:{port}/v1/integrations/github/webhook", method="POST", data=payload,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig, "X-GitHub-Event": "push"})
            with urllib.request.urlopen(r) as x: self.assertEqual(x.status, 202)
            bad = urllib.request.Request(f"http://127.0.0.1:{port}/v1/integrations/github/webhook", method="POST", data=payload,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=no", "X-GitHub-Event": "push"})
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 401)
        finally: proc.terminate()

    def test_real_env_wins_over_dotenv(self):
        tmp = tempfile.mkdtemp()
        pathlib.Path(tmp, ".env").write_text("PORT=1111\n")
        code = ("import importlib.util, os; spec = importlib.util.spec_from_file_location('srv', r'" + str(ROOT/"server/server.py") + "');"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(os.environ.get('PORT'))")
        r = subprocess.run([sys.executable, "-c", code], cwd=tmp, env={**os.environ, "PORT": "9999"}, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "9999", r.stderr)

if __name__ == "__main__":
    unittest.main()
