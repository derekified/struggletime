import pathlib, subprocess, sys, unittest
ROOT = pathlib.Path(__file__).parent.parent

class TestNoShadow(unittest.TestCase):
    def test_stdlib_inspect_not_shadowed_by_daemon_dir(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import inspect; assert hasattr(inspect, 'signature')"
        r = subprocess.run([sys.executable, "-c", code, str(ROOT / "daemon")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_daemon_scripts_help_runs(self):
        for script in ("tray.py", "gui.py", "helper.py", "daemon.py", "tracker_inspect.py"):
            r = subprocess.run([sys.executable, str(ROOT / "daemon" / script), "--help"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr[-400:]}")

if __name__ == "__main__":
    unittest.main()
