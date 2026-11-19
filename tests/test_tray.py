import importlib.util, json, pathlib, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("tray", ROOT / "daemon" / "tray.py")
tray = importlib.util.module_from_spec(spec); spec.loader.exec_module(tray)

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

    @unittest.skipIf(tray.pystray is None, "pystray/pillow not installed")
    def test_icon_renders(self):
        self.assertEqual(tray.make_icon(True).size, (64, 64))

if __name__ == "__main__":
    unittest.main()
