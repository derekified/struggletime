import importlib.util, pathlib, tempfile, unittest
ROOT = pathlib.Path(__file__).parent.parent

def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

project_link = load("project_link", "daemon/project_link.py")
native_ui = load("native_ui", "daemon/native_ui.py")
dcfg = load("config", "daemon/config.py")

class TestProjectLink(unittest.TestCase):
    def test_none_repo_option(self):
        self.assertEqual(project_link.NONE_REPO["repo_url"], None)
        self.assertIn("None", project_link.NONE_REPO["label"])

    def test_save_local_mapping(self):
        td = tempfile.mkdtemp()
        path = pathlib.Path(td) / "gui.json"
        project_link.save_local_mapping(
            {"server": dcfg.PUBLIC_SERVER, "token": "st_x", "handle": "u"},
            project_id="demo", local_path=td, path=path,
        )
        back = dcfg.load_settings(path)
        self.assertEqual(back["project"], "demo")
        self.assertEqual(back["repo"], td)

    def test_native_choose_stdin(self):
        self.assertTrue(callable(native_ui.choose))
        self.assertTrue(callable(native_ui.choose_folder))

if __name__ == "__main__":
    unittest.main()
