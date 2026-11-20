import importlib.util, json, pathlib, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "daemon"))

def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

editors = load("editors", "daemon/editors.py")

class TestEditors(unittest.TestCase):
    def test_builtin_matches_niche_ides(self):
        blob = "cursor helper\nwindsurf\ntrae\nkiro\nzed\nvscodium\nnvim\nhelix\nclaude\ncodex\nopenclaw\naider\ngoose\nopencode\ngemini\n"
        hits = editors.match_signatures(blob)
        labels = {h["editor"] for h in hits}
        for need in ("cursor", "windsurf", "trae", "kiro", "zed", "vscodium", "neovim", "helix", "claude-code", "codex", "openclaw"):
            self.assertIn(need, labels, need)
        agents = editors.detect_running_agents(blob)
        self.assertTrue(any(a in agents for a in ("claude", "codex", "openclaw", "aider")))

    def test_user_override_wins(self):
        td = tempfile.mkdtemp()
        path = pathlib.Path(td) / "editors.json"
        path.write_text(json.dumps({
            "signatures": [{"match": "weird-ide", "editor": "weird-ide", "kind": "ai_ide"}],
            "aliases": {"code": "vscode"},
        }))
        import os
        os.environ["AITIME_EDITORS_JSON"] = str(path)
        editors._CACHE["mtime"] = None
        editors._CACHE["sigs"] = None
        hits = editors.match_signatures("weird-ide main\ncode helper\n")
        self.assertEqual(hits[0]["editor"], "weird-ide")
        labels = [h["editor"] for h in hits]
        self.assertIn("vscode", labels)

    def test_classify_never_local_daemon(self):
        ed, ag, kind, src = editors.classify_active("human", {"editor": "cursor", "source": "adapter"})
        self.assertEqual(ed, "cursor")
        self.assertNotIn(ed, ("local-daemon", "untracked"))
        ed2, ag2, _, _ = editors.classify_active("ai", {})
        self.assertNotEqual(ed2, "local-daemon")

    def test_infer_unknown(self):
        found = editors.infer_unknown_editor("mycoolide\nbash\npython3\n")
        self.assertIn("mycoolide", found)
        self.assertNotIn("bash", found)

    def test_vim_not_false_positive_on_nvim(self):
        hits = editors.match_signatures("nvim --embed\n")
        labels = {h["editor"] for h in hits}
        self.assertIn("neovim", labels)
        self.assertNotIn("vim", labels)

if __name__ == "__main__":
    unittest.main()
