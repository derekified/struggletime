import json, os, pathlib, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent

AI_CLIS = ["claude", "codex", "aider", "opencode", "openclaw", "gemini", "goose", "amp", "crush", "copilot", "cursor-agent", "qwen"]

class TestCliIde(unittest.TestCase):
    def test_shell_hook_classifies_ai_clis(self):
        cmd = "source daemon/hooks.sh; " + "; ".join(f'echo "{c}=$(_aitime_category {c})"' for c in AI_CLIS)
        r = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for line in r.stdout.splitlines():
            self.assertTrue(line.endswith("=ai_cli"), line)

    def test_shell_hook_classifies_build_tools(self):
        r = subprocess.run(["bash", "-c", "source daemon/hooks.sh; echo $(_aitime_category pytest) $(_aitime_category cargo) $(_aitime_category git)"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "test_runner test_runner git")

    def test_vscode_extension_is_valid_js(self):
        r = subprocess.run(["node", "--check", str(ROOT/"adapters/vscode/extension.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ide_style_bursts_counted_without_any_plugin(self):
        # any editor (vscode, cursor, zed, neovim) saving files looks like this to layer 1
        tmp = tempfile.mkdtemp(); repo = pathlib.Path(tmp) / "ideproj"; repo.mkdir()
        for cmd in (["git","init","-q",str(repo)], ["git","-C",str(repo),"config","user.email","t@t.t"], ["git","-C",str(repo),"config","user.name","t"]): subprocess.run(cmd)
        (repo/"app.py").write_text("a=1\n"); subprocess.run(["git","-C",str(repo),"add","."]); subprocess.run(["git","-C",str(repo),"commit","-qm","i"])
        env = {**os.environ, "AITIME_EXEC_LOG": str(pathlib.Path(tmp)/"none.jsonl")}
        proc = subprocess.Popen([sys.executable, str(ROOT/"daemon/daemon.py"), "--repo", str(repo), "--project", "ideproj",
                                 "--bucket-secs", "3", "--once", "--server", ""],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        import time; time.sleep(1.2)
        (repo/"app.py").write_text("a=2\n")  # a single editor save: 1 file, human burst shape
        out, _ = proc.communicate(timeout=30)
        self.assertIn("bucket", out)
        ledger = json.loads((repo/".aitime-ledger.json").read_text())
        b = ledger["pending"][0]
        self.assertGreaterEqual(b["counts"]["human_edits"], 1, b["counts"])
        self.assertTrue(b["files"], "anonymized file metadata expected")
        self.assertNotIn("app.py", json.dumps(b["files"]))

if __name__ == "__main__":
    unittest.main()
