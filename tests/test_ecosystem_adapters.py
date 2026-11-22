"""Sanitized adapter ingestion, process fallback labels, and cache/lock ignores."""
import importlib.util, json, pathlib, sys, tempfile, unittest
from pathlib import Path

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "daemon"))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


editors = load("editors_eco", "daemon/editors.py")
agent_event = load("agent_event_eco", "daemon/agent_event.py")
daemon = load("daemon_eco", "daemon/daemon.py")


class TestProcessFallbackLabels(unittest.TestCase):
    def test_creative_cad_and_ide_signatures(self):
        blob = "\n".join([
            "kicad", "pcbnew", "eeschema", "gerbview",
            "blender", "freecad", "openscad",
            "aseprite", "krita", "gimp", "figma",
            "xcode", "arduino-ide", "hx",
            "jupyter-lab", "rstudio", "obsidian",
            "godot", "unity", "clion", "rustrover", "rider", "android studio",
        ])
        labels = {h["editor"] for h in editors.match_signatures(blob)}
        for need in (
            "kicad", "blender", "freecad", "openscad", "aseprite", "krita",
            "gimp", "figma", "xcode", "arduino", "helix", "jupyter",
            "rstudio", "obsidian", "godot", "unity", "jetbrains",
        ):
            self.assertIn(need, labels, need)

    def test_hx_maps_to_helix_not_noise(self):
        hits = editors.match_signatures("hx --health\nbash\n")
        labels = {h["editor"] for h in hits}
        self.assertIn("helix", labels)
        self.assertEqual(editors.detect_running_editors("hx\n")[0], "helix")


class TestCacheLockIgnores(unittest.TestCase):
    def test_ignored_paths(self):
        cases = [
            (".godot/imported/foo", True),
            ("sprites/player.png.import", True),
            ("Library/ArtifactDB", True),
            ("Temp/Worker0", True),
            ("Obj/Debug", True),
            ("Intermediate/Build", True),
            ("Saved/Logs", True),
            ("Binaries/Win64", True),
            ("board-backups/old", True),
            ("~lock.lck", True),
            ("schematic-bak", True),
            ("fp-info-cache", True),
            ("scene.blend1", True),
            ("scene.blend2", True),
            ("DerivedData/ModuleCache", True),
            ("App.xcworkspace/xcuserdata/me", True),
            (".idea/workspace.xml", True),
            ("src/main.py", False),
            ("assets/player.png", False),
            ("project.godot", False),
        ]
        for rel, expect in cases:
            self.assertEqual(daemon.ignored_rel_path(rel), expect, rel)

    def test_ignored_dir_names(self):
        for name in (".godot", "Library", "Temp", "Obj", "Intermediate", "Saved",
                     "Binaries", ".idea", "DerivedData", "xcuserdata", "foo-backups"):
            self.assertTrue(daemon.ignored_dir_name(name), name)
        self.assertFalse(daemon.ignored_dir_name("src"))
        self.assertFalse(daemon.ignored_dir_name("addons"))

    def test_snapshot_skips_cache_noise(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "main.py").write_text("print(1)\n")
            (repo / ".godot").mkdir()
            (repo / ".godot" / "imported").mkdir()
            (repo / ".godot" / "imported" / "x").write_text("x")
            (repo / "Library").mkdir()
            (repo / "Library" / "ArtifactDB").write_text("x")
            (repo / "sprite.png.import").write_text("x")
            (repo / "scene.blend1").write_text("x")
            (repo / "~board.lck").write_text("x")
            snap = daemon.snapshot(repo)
            keys = set(snap)
            self.assertIn("src/main.py", keys)
            self.assertNotIn("sprite.png.import", keys)
            self.assertNotIn("scene.blend1", keys)
            self.assertNotIn("~board.lck", keys)
            self.assertFalse(any(k.startswith(".godot") for k in keys))
            self.assertFalse(any(k.startswith("Library") for k in keys))


class TestAdapterEventIngestion(unittest.TestCase):
    def test_play_and_save_event_types(self):
        ok, e = agent_event.validate_event({
            "event_type": "play", "editor": "godot", "agent": "human", "exit_code": 0,
        })
        self.assertTrue(ok)
        self.assertEqual(e["category"], "test_runner")
        self.assertEqual(e["editor"], "godot")
        ok, e = agent_event.validate_event({
            "event_type": "save", "editor": "unity", "agent": "human",
            "op": "modified", "files_touched_count": 2,
        })
        self.assertTrue(ok)
        self.assertEqual(e["category"], "editor")
        self.assertEqual(e["files_touched_count"], 2)

    def test_classify_exec_from_adapter_jsonl(self):
        for editor, agent, et in (
            ("neovim", "human", "edit"),
            ("jetbrains", "unknown-agent", "edit"),
            ("godot", "human", "play"),
            ("unity", "human", "focus"),
        ):
            line = json.dumps({
                "ts": 1, "event_type": et, "editor": editor, "agent": agent,
                "category": "test_runner" if et == "play" else "editor",
                "files_touched_count": 0 if et in ("focus", "play") else 1,
                "source": "adapter",
                "path": "/must/not/leak.py",
            })
            # path is forbidden for validate_event path; classify_exec strips FORBIDDEN first
            e = daemon.classify_exec(line)
            self.assertEqual(e["editor"], editor)
            self.assertEqual(e["source"], "adapter")
            self.assertNotIn("path", e)

    def test_emit_rejects_paths(self):
        ok, err = agent_event.validate_event({
            "event_type": "edit", "editor": "neovim", "path": "/tmp/x.py",
        })
        self.assertFalse(ok)
        self.assertIn("forbidden", err)

    def test_adapter_trees_exist(self):
        expected = [
            "adapters/neovim/lua/struggletime/init.lua",
            "adapters/godot/addons/struggletime/plugin.gd",
            "adapters/godot/addons/struggletime/plugin.cfg",
            "adapters/jetbrains/src/main/kotlin/com/struggletime/StruggleTimeStartup.kt",
            "adapters/jetbrains/src/main/resources/META-INF/plugin.xml",
            "adapters/unity/Editor/StruggleTimeTracker.cs",
        ]
        for rel in expected:
            self.assertTrue((ROOT / rel).is_file(), rel)
            text = (ROOT / rel).read_text().lower()
            for bad in ("prompt_text", "transcript", "keystroke"):
                self.assertNotIn(bad, text, rel)


if __name__ == "__main__":
    unittest.main()
