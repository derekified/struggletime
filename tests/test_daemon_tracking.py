#!/usr/bin/env python3
"""Daemon multi-project watching + verify active-span / classification fixes."""
import json, os, pathlib, sys, tempfile, time, unittest
from pathlib import Path

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))

import verify
import config as dcfg
import daemon as daemon_mod


def _counts(**kw):
    base = {
        "human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0,
        "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
    }
    base.update(kw)
    return base


def _bucket(counts=None, **top):
    c = _counts(**(counts or {}))
    b = {
        "v": 1, "bucket_id": "b" * 8, "install_id": "i", "project_id": "p",
        "repo_remote_hash": None, "bucket_start_wall": 0, "bucket_secs": 300,
        "mono_delta_ms": 300000, "counts": c,
        "git": {"head_commit": "abc", "lines_added": 5, "lines_deleted": 1, "dirty_files": 1},
        "files": [], "formula_version": "v0.2.0-alpha", "prev_hash": verify.GENESIS,
    }
    b.update(top)
    return b


class TestActiveSpanWeight(unittest.TestCase):
    def test_no_idle_cliff_at_240(self):
        # Old cliff zeroed the whole bucket; now a short burst still gets credit via active_secs
        b = _bucket({
            "human_edits": 4, "idle_secs": 250, "active_secs": 50,
            "first_event_ts": 1000, "last_event_ts": 1050,
        })
        w, state = verify.weight(b)
        self.assertGreater(w, 0.0)
        self.assertNotEqual(state, "IDLE")
        self.assertEqual(verify.billable_secs(b), 50)
        hours = verify.score([b])[1]
        self.assertAlmostEqual(hours, (50 * w) / 3600)

    def test_gradual_decay_5_to_12_min(self):
        self.assertEqual(verify.decay_weight(300), 0.70)
        self.assertGreater(verify.decay_weight(500), 0.0)
        self.assertLess(verify.decay_weight(500), 0.70)
        self.assertEqual(verify.decay_weight(720), 0.0)

    def test_silent_empty_still_decays(self):
        seq = [_bucket() for _ in range(5)]
        ws = verify.weight_sequence(seq)
        self.assertEqual(ws[0], (0.70, "SILENT_DEBUG"))
        self.assertEqual(ws[4][0], 0.0)

    def test_struggle_loop_beats_file_count(self):
        b = _bucket({
            "human_edits": 2, "ai_writes": 8, "executions": 3, "exec_failures": 1, "commits": 0,
        })
        w, state = verify.weight(b)
        self.assertEqual(w, 1.0)
        self.assertIn(state, ("AI_STRUGGLE", "STRUGGLE_LOOP"))

    def test_ai_struggle_from_reedit_and_fail_pass(self):
        b = _bucket({
            "ai_writes": 4, "human_edits": 1, "executions": 3, "exec_failures": 2,
            "same_file_reedit_count": 3, "ai_retry_loops": 2,
            "test_fail_to_pass_transitions": 1, "human_recovery_edits": 1,
            "failed_exec_count": 2, "agent_turn_churn": 3,
        })
        w, state = verify.weight(b)
        self.assertEqual((w, state), (1.0, "AI_STRUGGLE"))
        self.assertEqual(verify.validate_allowlist(b), [])

    def test_bulk_dump_still_discounted(self):
        b = _bucket({
            "ai_writes": 12, "human_edits": 0, "executions": 0, "dwell_diff_secs": 0,
            "same_file_reedit_count": 0, "ai_retry_loops": 0,
        })
        w, state = verify.weight(b)
        self.assertEqual((w, state), (0.20, "OPEN_LOOP_AGENT_DUMP"))

    def test_formatter_bulk_not_open_loop(self):
        b = _bucket({"ai_writes": 12, "human_edits": 0, "executions": 0})
        b["exec_categories"] = {"formatter": 1}
        w, state = verify.weight(b)
        self.assertNotEqual(state, "OPEN_LOOP_AGENT_DUMP")
        self.assertEqual(state, "AI_UNREVIEWED")

    def test_open_loop_still_discounted(self):
        b = _bucket({"ai_writes": 12, "human_edits": 0, "executions": 0, "dwell_diff_secs": 0})
        w, state = verify.weight(b)
        self.assertEqual((w, state), (0.20, "OPEN_LOOP_AGENT_DUMP"))

    def test_allowlist_accepts_active_span_fields(self):
        b = _bucket({"active_secs": 90, "first_event_ts": 1, "last_event_ts": 91, "idle_secs": 10})
        self.assertEqual(verify.validate_allowlist(b), [])


class TestClassifyEditBatch(unittest.TestCase):
    def test_many_files_incremental_is_human(self):
        kind = daemon_mod.classify_edit_batch(
            8, edit_ticks=4, lines_hint=40, dt_sec=1.0,
            recent_cats={}, attr_hint={"agent": "human"},
        )
        self.assertEqual(kind, "human")

    def test_formatter_run_is_human(self):
        kind = daemon_mod.classify_edit_batch(
            20, edit_ticks=1, lines_hint=500, dt_sec=0.5,
            recent_cats={"formatter": 1}, attr_hint={},
        )
        self.assertEqual(kind, "human")

    def test_single_tick_bulk_velocity_is_ai(self):
        kind = daemon_mod.classify_edit_batch(
            10, edit_ticks=1, lines_hint=400, dt_sec=0.5,
            recent_cats={}, attr_hint={},
        )
        self.assertEqual(kind, "ai")

    def test_bulk_with_tests_is_human(self):
        kind = daemon_mod.classify_edit_batch(
            10, edit_ticks=1, lines_hint=400, dt_sec=0.5,
            recent_cats={"test_runner": 1}, attr_hint={},
        )
        self.assertEqual(kind, "human")


class TestMultiProjectConfig(unittest.TestCase):
    def test_linked_projects_accumulates(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "aitime"
            cfg = Path(td) / "cfg"
            home.mkdir(); cfg.mkdir()
            old = (dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS, dcfg.LEGACY_GUI_SETTINGS)
            try:
                dcfg.HOME = home
                dcfg.CONFIG_HOME = cfg
                dcfg.GUI_SETTINGS = cfg / "settings.json"
                dcfg.LEGACY_GUI_SETTINGS = home / "gui.json"
                s = dcfg.upsert_linked_project({}, project_id="alpha", local_path=str(Path(td) / "a"))
                (Path(td) / "a").mkdir()
                (Path(td) / "b").mkdir()
                s = dcfg.upsert_linked_project(s, project_id="beta", local_path=str(Path(td) / "b"))
                dcfg.save_settings(s)
                loaded = dcfg.load_settings()
                ids = {p["id"] for p in loaded["projects"]}
                self.assertEqual(ids, {"alpha", "beta"})
                self.assertEqual(loaded["project"], "beta")
            finally:
                dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS, dcfg.LEGACY_GUI_SETTINGS = old

    def test_resolve_watch_list_two_repos(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "proj-a"; b = Path(td) / "proj-b"
            a.mkdir(); b.mkdir()
            rt = {"server": "", "token": "", "projects": [
                {"id": "A", "path": str(a)},
                {"id": "B", "path": str(b)},
            ], "repo": "", "project": "", "bucket_mins": 5}
            watchers = daemon_mod.resolve_watch_list([], {}, None, rt)
            self.assertEqual(len(watchers), 2)
            self.assertEqual({w["project"] for w in watchers}, {"A", "B"})

    def test_match_project_longest_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; nested = root / "nested"
            nested.mkdir(parents=True)
            w_root = daemon_mod.make_watcher(root, "root")
            w_nested = daemon_mod.make_watcher(nested, "nested")
            hit = daemon_mod.match_project(str(nested / "file.py"), [w_root, w_nested])
            self.assertEqual(hit["project"], "nested")


class TestEventRouting(unittest.TestCase):
    def test_cwd_routed_but_not_in_cleaned_keys(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"; repo.mkdir()
            line = json.dumps({
                "name": "pytest", "exit_code": 1, "cwd": str(repo), "ts": int(time.time()),
            })
            e = daemon_mod.classify_exec(line)
            self.assertEqual(e["category"], "test_runner")
            self.assertEqual(e["_cwd"], str(repo))
            self.assertNotIn("cwd", {k for k in e if not k.startswith("_")})


if __name__ == "__main__":
    unittest.main()
