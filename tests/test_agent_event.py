import importlib.util, json, pathlib, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent

def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

agent_event = load("agent_event", "daemon/agent_event.py")

class TestAgentEvent(unittest.TestCase):
    def test_validate_accepts_allowlisted(self):
        ok, e = agent_event.validate_event({
            "ts": 1, "event_type": "edit", "editor": "cursor", "agent": "human",
            "op": "modified", "exit_code": 0, "files_touched_count": 1,
        })
        self.assertTrue(ok)
        self.assertEqual(e["category"], "editor")
        self.assertEqual(e["editor"], "cursor")

    def test_validate_rejects_forbidden(self):
        ok, err = agent_event.validate_event({"event_type": "edit", "path": "/secret.py"})
        self.assertFalse(ok)
        self.assertIn("forbidden", err)
        ok, err = agent_event.validate_event({"prompt_text": "write me code"})
        self.assertFalse(ok)

    def test_emit_and_cli(self):
        td = tempfile.mkdtemp()
        log = pathlib.Path(td) / "agent_events.jsonl"
        cleaned = agent_event.emit({
            "event_type": "tool", "editor": "codex", "agent": "codex", "exit_code": 0,
        }, path=log)
        self.assertEqual(cleaned["category"], "ai_cli")
        lines = log.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["editor"], "codex")
        import os
        env = os.environ.copy()
        env["AITIME_AGENT_EVENTS"] = str(log)
        r = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "agent_event.py"),
             "--editor", "cursor", "--agent", "human", "--event-type", "edit", "--op", "modified"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(log.read_text().strip().splitlines()), 2)

    def test_no_mcp_server_code(self):
        needles = ("mcp server", "json-rpc", "jsonrpc", "model context protocol", "@modelcontextprotocol")
        for rel in ("daemon/daemon.py", "daemon/agent_event.py", "adapters/vscode/extension.js",
                    "adapters/openclaw/adapter.py"):
            text = (ROOT / rel).read_text().lower()
            for n in needles:
                self.assertNotIn(n, text, f"{rel} contains {n}")

if __name__ == "__main__":
    unittest.main()
