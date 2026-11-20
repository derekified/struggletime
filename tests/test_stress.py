import importlib.util, pathlib, unittest
ROOT = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("stress_test", ROOT / "daemon" / "stress_test.py")
st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)

class TestStress(unittest.TestCase):
    def test_project_id_shape(self):
        pid = st.project_id("abcdef", 42)
        self.assertEqual(pid, "stsabcdef0042")
        self.assertLessEqual(len(pid), 40)
        self.assertTrue(pid.startswith(st.ID_PREFIX))

    def test_percentile(self):
        self.assertEqual(st.percentile([], 50), 0.0)
        self.assertEqual(st.percentile([10], 95), 10.0)
        self.assertEqual(st.percentile([1, 2, 3, 4, 5], 50), 3.0)

    def test_bucket_hash_chain(self):
        b1 = st.make_bucket("stsabcdef0001", st.GENESIS, 1_700_000_000, 300, st.human_counts())
        self.assertEqual(len(b1["hash"]), 64)
        b2 = st.make_bucket("stsabcdef0001", b1["hash"], 1_700_000_300, 300, st.agent_counts())
        self.assertEqual(b2["prev_hash"], b1["hash"])

if __name__ == "__main__":
    unittest.main()
