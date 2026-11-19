import json, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from verify import verify_chain, score

ROOT = pathlib.Path(__file__).parent.parent

class TestVerify(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "examples" / name).read_text())

    def test_closed_loop_full_weight(self):
        _, hours = score(self.load("session-closed-loop.json"))
        self.assertAlmostEqual(hours, 0.25)

    def test_open_loop_discounted(self):
        _, hours = score(self.load("session-open-loop.json"))
        self.assertLess(hours, 0.05)

    def test_tamper_detected(self):
        self.assertTrue(verify_chain(self.load("session-tampered.json")))

    def test_clean_chain_valid(self):
        self.assertEqual(verify_chain(self.load("session-closed-loop.json")), [])

if __name__ == "__main__":
    unittest.main()
