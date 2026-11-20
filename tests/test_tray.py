import importlib.util, json, os, pathlib, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("tray", ROOT / "daemon" / "tray.py")
tray = importlib.util.module_from_spec(spec); spec.loader.exec_module(tray)
cfg_spec = importlib.util.spec_from_file_location("dcfg", ROOT / "daemon" / "config.py")
dcfg = importlib.util.module_from_spec(cfg_spec); cfg_spec.loader.exec_module(dcfg)

class TestTray(unittest.TestCase):
    def test_pending_count(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tray.pending_count(d), 0)
            (pathlib.Path(d) / ".aitime-ledger.json").write_text(json.dumps({"pending": [{"a": 1}, {"b": 2}]}))
            self.assertEqual(tray.pending_count(d), 2)

    def test_weighted_title(self):
        self.assertEqual(tray.weighted_title(0), "0m")
        self.assertEqual(tray.weighted_title(0.7), "42m")
        self.assertEqual(tray.weighted_title(1.25), "1h 15m")
        self.assertEqual(tray.weighted_title(2.0), "2h")
        self.assertEqual(tray.weighted_title(1.25, tracking=False), "1h 15m")

    def test_aitime_gui_json_and_pause(self):
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / "aitime"
            cfg = pathlib.Path(d) / "config"
            home.mkdir(); cfg.mkdir()
            gui = cfg / "settings.json"
            gui.write_text(json.dumps({"handle": "rowan", "repo": "/tmp/x", "server": "http://127.0.0.1:18765", "bucket_mins": 5}))
            os.environ["STRUGGLETIME_HOME"] = str(home)
            os.environ["STRUGGLETIME_CONFIG"] = str(cfg)
            for k in ("STRUGGLETIME_SERVER", "AITIME_SERVER", "OAUTH_REDIRECT_BASE",
                      "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL"):
                os.environ.pop(k, None)
            dcfg.HOME = home
            dcfg.CONFIG_HOME = cfg
            dcfg.GUI_SETTINGS = gui
            dcfg.LEGACY_GUI_SETTINGS = home / "gui.json"
            dcfg.PAUSE_FLAG = home / "paused"
            s = dcfg.load_settings(gui)
            self.assertEqual(s["handle"], "rowan")
            self.assertEqual(s["server"], "http://127.0.0.1:18765")
            self.assertFalse(hasattr(dcfg, "WAKATIME_CFG"))
            self.assertFalse(hasattr(dcfg, "read_wakatime_cfg"))
            dcfg.set_paused(True)
            self.assertTrue(dcfg.is_paused())
            dcfg.set_paused(False)
            self.assertFalse(dcfg.is_paused())

    def test_migrate_legacy_gui_json(self):
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / "aitime"
            cfg = pathlib.Path(d) / "config"
            home.mkdir()
            legacy = home / "gui.json"
            legacy.write_text(json.dumps({"handle": "mig", "repo": "/tmp/r", "server": "http://127.0.0.1:9", "bucket_mins": 5, "token": "st_x"}))
            for k in ("STRUGGLETIME_SERVER", "AITIME_SERVER", "OAUTH_REDIRECT_BASE",
                      "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL"):
                os.environ.pop(k, None)
            os.environ["STRUGGLETIME_HOME"] = str(home)
            os.environ["STRUGGLETIME_CONFIG"] = str(cfg)
            dcfg.HOME = home
            dcfg.CONFIG_HOME = cfg
            dcfg.GUI_SETTINGS = cfg / "settings.json"
            dcfg.LEGACY_GUI_SETTINGS = legacy
            dcfg.PAUSE_FLAG = home / "paused"
            s = dcfg.load_settings()
            self.assertEqual(s["handle"], "mig")
            self.assertTrue((cfg / "settings.json").exists())
            dcfg.save_settings({**s, "bucket_mins": 7})
            self.assertEqual(json.loads((cfg / "settings.json").read_text())["bucket_mins"], 7)
            self.assertTrue(legacy.exists())  # compat mirror

    def test_normalize_ngrok_placeholder(self):
        self.assertEqual(
            dcfg.normalize_server("<NGROK, nothing after it>"),
            dcfg.PUBLIC_SERVER.rstrip("/"),
        )
        self.assertEqual(dcfg.normalize_server(""), dcfg.PUBLIC_SERVER.rstrip("/"))
        self.assertEqual(dcfg.normalize_server("not-a-url"), dcfg.PUBLIC_SERVER.rstrip("/"))
        self.assertTrue(dcfg.PUBLIC_SERVER.startswith("https://rap-minds-question-representatives.trycloudflare.com"))
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / "aitime"
            cfg = pathlib.Path(d) / "config"
            home.mkdir(); cfg.mkdir()
            gui = cfg / "settings.json"
            gui.write_text(json.dumps({"handle": "x", "repo": "/tmp", "server": "<NGROK, nothing after it>", "bucket_mins": 5}))
            for k in ("STRUGGLETIME_SERVER", "AITIME_SERVER", "OAUTH_REDIRECT_BASE",
                      "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL"):
                os.environ.pop(k, None)
            os.environ["STRUGGLETIME_HOME"] = str(home)
            os.environ["STRUGGLETIME_CONFIG"] = str(cfg)
            dcfg.HOME = home
            dcfg.CONFIG_HOME = cfg
            dcfg.GUI_SETTINGS = gui
            dcfg.LEGACY_GUI_SETTINGS = home / "gui.json"
            s = dcfg.load_settings(gui)
            self.assertEqual(s["server"], dcfg.PUBLIC_SERVER.rstrip("/"))
            saved = json.loads(gui.read_text())
            self.assertEqual(saved["server"], dcfg.PUBLIC_SERVER.rstrip("/"))

    def test_has_display_and_headless_helpers(self):
        self.assertIsInstance(tray.has_display(), bool)
        self.assertIn("pip install", tray.INSTALL_HINT)

    def test_login_and_link_helpers_exist(self):
        self.assertTrue(callable(tray.TrayCore.set_api_key))
        self.assertTrue(callable(tray.TrayCore.clear_api_key))
        self.assertTrue(callable(tray.TrayCore.link_project))
        self.assertTrue(callable(tray.TrayCore.reload_settings))
        self.assertTrue(callable(tray.git_remote_url))
        self.assertTrue(callable(tray.prompt_text))
        self.assertFalse(hasattr(tray.TrayCore, "open_login"))
        self.assertFalse(hasattr(tray.TrayCore, "apply_auth_secret"))
        src = (ROOT / "daemon" / "tray.py").read_text()
        low = src.lower()
        self.assertIn("set / link api key", low)
        self.assertIn("get api key in browser", low)
        self.assertIn("link / create project", low)
        self.assertIn("project_link", src)
        self.assertNotIn("sign in with slack", low)
        self.assertNotIn("paste auth", low)
        self.assertNotIn("import tkinter", src)
        self.assertNotIn("_PROMPT_SCRIPT", src)
        self.assertIn("native_ui", src)

    def test_browser_auth_helpers(self):
        ba_spec = importlib.util.spec_from_file_location("browser_auth", ROOT / "daemon" / "browser_auth.py")
        browser_auth = importlib.util.module_from_spec(ba_spec); ba_spec.loader.exec_module(browser_auth)
        self.assertTrue(callable(browser_auth.open_default_browser))
        self.assertTrue(callable(browser_auth.DesktopLogin))
        self.assertTrue(browser_auth.valid_desktop_redirect("http://127.0.0.1:9/callback"))
        self.assertFalse(browser_auth.valid_desktop_redirect("https://evil.example/callback"))
        self.assertFalse(browser_auth.valid_desktop_redirect("http://example.com/callback"))
        self.assertEqual(browser_auth.normalize_browser_url(" https://x.example/a "), "https://x.example/a")
        with self.assertRaises(ValueError):
            browser_auth.normalize_browser_url("/relative")
        with self.assertRaises(ValueError):
            browser_auth.normalize_browser_url("ftp://nope")

    @unittest.skipIf(tray.pystray is None, "pystray/pillow not installed")
    def test_icon_renders(self):
        self.assertEqual(tray.make_icon(True).size, (64, 64))
        self.assertEqual(tray.make_icon(True, paused=True).size, (64, 64))

if __name__ == "__main__":
    unittest.main()
