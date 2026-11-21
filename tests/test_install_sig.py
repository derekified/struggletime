#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
import json, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import install_sig
from verify import bucket_hash, GENESIS, validate_allowlist, FORMULA_VERSION

class TestInstallSig(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            loaded = install_sig.load_or_create_keypair(cfg)
            self.assertIsNotNone(loaded)
            priv, _pub, pub_raw, iid = loaded
            self.assertTrue(iid.startswith("inst_"))
            self.assertEqual(len(pub_raw), 32)
            h = "a" * 64
            sig = install_sig.sign_hash(h, priv)
            self.assertTrue(sig)
            self.assertTrue(install_sig.verify_signature(h, sig, pub_raw))
            self.assertFalse(install_sig.verify_signature("b" * 64, sig, pub_raw))

    def test_bucket_allowlist_with_sig_fields(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            loaded = install_sig.load_or_create_keypair(cfg)
            priv, _pub, pub_raw, iid = loaded
            b = {
                "v": 1, "bucket_id": "bbbbbbbb", "install_id": iid, "project_id": "p",
                "repo_remote_hash": None, "bucket_start_wall": 1, "bucket_secs": 60,
                "mono_delta_ms": 60000,
                "counts": {"human_edits": 1, "ai_writes": 0, "prompts": 0, "executions": 0,
                           "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
                           "active_secs": 60},
                "git": {"head_commit": None, "lines_added": 0, "lines_deleted": 0, "dirty_files": 0},
                "files": [], "formula_version": FORMULA_VERSION, "prev_hash": GENESIS,
                "install_pubkey": install_sig._b64(pub_raw), "sig_alg": "ed25519",
                "os": "linux", "os_detail": {"id": "linux", "release": "1", "arch": "x64"},
                "editors": {}, "agents": {}, "languages": {}, "exec_categories": {},
            }
            b["hash"] = bucket_hash(b)
            b["signature"] = install_sig.sign_hash(b["hash"], priv)
            self.assertEqual(validate_allowlist(b), [])
            self.assertTrue(install_sig.verify_signature(b["hash"], b["signature"], pub_raw))

if __name__ == "__main__":
    unittest.main()
