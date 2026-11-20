import importlib.util, pathlib, unittest
ROOT = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("native_ui", ROOT / "daemon" / "native_ui.py")
native_ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(native_ui)

class TestNativeUi(unittest.TestCase):
    def test_backend_and_display(self):
        self.assertIsInstance(native_ui.has_display(), bool)
        self.assertIsInstance(native_ui.backend_name(), str)
        self.assertTrue(callable(native_ui.prompt))
        self.assertTrue(callable(native_ui.alert))
        self.assertTrue(callable(native_ui.confirm))
        self.assertTrue(callable(native_ui.choose_folder))

    def test_no_tkinter_import(self):
        src = (ROOT / "daemon" / "native_ui.py").read_text()
        self.assertNotIn("import tkinter", src)
        self.assertNotIn("from tkinter", src)
        self.assertNotIn("tkinter", native_ui.__dict__)

    def test_mac_escape(self):
        self.assertIn('\\"', native_ui._mac_escape('say "hi"'))

if __name__ == "__main__":
    unittest.main()
