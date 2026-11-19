import json, os, pathlib, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "adapters" / "openclaw"))
from adapter import classify, scan

class TestOpenClaw(unittest.TestCase):
    def test_classify_shapes_only(self):
        self.assertEqual(classify({"role": "user", "content": "secret prompt"})["verb"], "prompt")
        self.assertEqual(classify({"type": "message", "content": [{"type": "toolCall", "name": "exec"}]})["verb"], "tool")
        self.assertEqual(classify({"type": "toolResult", "isError": True})["exit_code"], 1)
        self.assertIsNone(classify({"role": "assistant", "content": "hello"}))

    def test_adapter_never_copies_content(self):
        tmp = tempfile.mkdtemp(); sessions = pathlib.Path(tmp) / "s"; sessions.mkdir()
        (sessions / "abc.jsonl").write_text("\n".join([
            json.dumps({"role": "user", "content": "TOPSECRET_PROMPT"}),
            json.dumps({"type": "toolCall", "name": "write", "args": {"path": "secret.py"}}),
            json.dumps({"type": "toolResult", "isError": True, "output": "TOPSECRET_OUTPUT"}),
        ]))
        state = {}
        events = scan(sessions, state)
        self.assertEqual([e["verb"] for e in events], ["prompt", "tool", "tool_result"])
        blob = json.dumps(events)
        self.assertNotIn("TOPSECRET", blob); self.assertNotIn("secret.py", blob)
        self.assertEqual(scan(sessions, state), [])

    def test_adapter_cli_once(self):
        tmp = tempfile.mkdtemp(); sessions = pathlib.Path(tmp) / "s"; sessions.mkdir()
        (sessions / "x.jsonl").write_text(json.dumps({"role": "user", "content": "hi"}) + "\n")
        env = {**os.environ, "AITIME_EXEC_LOG": f"{tmp}/exec.jsonl"}
        r = subprocess.run([sys.executable, str(ROOT/"adapters/openclaw/adapter.py"), "--sessions-dir", str(sessions), "--once"],
                           capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = pathlib.Path(f"{tmp}/exec.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1); self.assertNotIn("hi\"", json.loads(lines[0]).get("verb", ""))

if __name__ == "__main__":
    unittest.main()
