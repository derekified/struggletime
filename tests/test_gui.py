import importlib.util, pathlib, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("gui", ROOT / "daemon" / "gui.py")
gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)

class TestGui(unittest.TestCase):
    def test_settings_roundtrip(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "gui.json"
        s = {"handle": "rowan", "repo": "/tmp/r", "server": "http://x:1", "bucket_mins": 5, "extra": "dropped"}
        gui.save_settings(s, p)
        back = gui.load_settings(p)
        self.assertEqual(back["handle"], "rowan"); self.assertNotIn("extra", back)

    def test_validate(self):
        self.assertTrue(gui.validate({"handle": "", "repo": "", "server": "x"}))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gui.validate({"handle": "rowan", "repo": d, "server": "http://127.0.0.1:18765"}), [])

    def test_daemon_cmd(self):
        cmd = gui.daemon_cmd({"handle": "rowan", "repo": "/tmp/r", "server": "http://127.0.0.1:18765/", "bucket_mins": 5})
        self.assertIn("daemon.py", cmd[1]); self.assertIn("300", cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], "rowan")

    def test_no_token_paste_ui(self):
        src = (ROOT / "daemon" / "gui.py").read_text()
        low = src.lower()
        self.assertNotIn("paste auth", low)
        self.assertNotIn("askstring", low)
        self.assertIn("sign in with slack", low)
        self.assertIn("browser_auth", src)

if __name__ == "__main__":
    unittest.main()
