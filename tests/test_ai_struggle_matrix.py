#!/usr/bin/env python3
"""Isolated multi-IDE / multi-agent AI struggle detection matrix.

Drives daemon internals against temp repos under /tmp only — never touches
~/.aitime or ~/.config/struggletime. Stdlib + repo modules.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))

import verify
import daemon as daemon_mod
import editors as edreg
import agent_event as agent_evt

STRUGGLE_STATES = frozenset({"AI_STRUGGLE", "STRUGGLE_LOOP", "CLOSED_LOOP"})
MATRIX_ROWS = []  # filled when run as __main__ or collect_matrix()


def _clear_editor_cache():
    edreg._CACHE["mtime"] = None
    edreg._CACHE["sigs"] = None
    edreg._CACHE["blob"] = None
    edreg._CACHE["blob_at"] = 0.0


def _git_init(repo: Path):
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "matrix@test.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Matrix"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# matrix\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _evt(now, *, category, editor=None, agent=None, exit_code=0, files_touched_count=0,
         event_type="exec", verb="", op=None, project_id=None, name=None):
    e = {
        "category": category,
        "event_type": event_type,
        "verb": verb or event_type,
        "exit_code": exit_code,
        "files_touched_count": files_touched_count,
        "ts": now,
        "source": "adapter",
    }
    if editor:
        e["editor"] = editor
    if agent:
        e["agent"] = agent
    if op:
        e["op"] = op
    if project_id:
        e["project_id"] = project_id
    if name:
        e["name"] = name
    return e


def _struggle_counters(c):
    keys = (
        "same_file_reedit_count", "ai_retry_loops", "failed_exec_count",
        "test_fail_to_pass_transitions", "agent_turn_churn", "human_recovery_edits",
    )
    return {k: int(c.get(k) or 0) for k in keys}


def _inspect_ok(repo: Path, buckets):
    """Mirror tracker_inspect checks: allowlist + chain + no salt in payload."""
    chain_errs = verify.verify_chain(buckets)
    allow_errs = []
    for i, b in enumerate(buckets):
        allow_errs.extend(f"bucket {i}: {e}" for e in verify.validate_allowlist(b))
    payload = json.dumps(buckets)
    ledger = json.loads((repo / ".aitime-ledger.json").read_text())
    salt = ledger.get("salt") or ""
    salt_leak = bool(salt) and salt in payload
    return chain_errs, allow_errs, salt_leak


def _row(scenario, label, mode, bucket, ok, note=""):
    c = bucket.get("counts") or {}
    w, state = verify.weight(bucket)
    weighted = verify.billable_secs(bucket) * w
    sc = _struggle_counters(c)
    chain_ok = "OK" if ok.get("chain") else "FAIL"
    allow_ok = "OK" if ok.get("allowlist") else "FAIL"
    eds = ",".join((bucket.get("editors") or {}).keys()) or "-"
    ags = ",".join((bucket.get("agents") or {}).keys()) or "-"
    return {
        "scenario": scenario,
        "label": label,
        "mode": mode,
        "editor": eds,
        "agent": ags,
        "state": state,
        "weight": w,
        "weighted_secs": round(weighted, 2),
        "counters": sc,
        "allowlist": allow_ok,
        "chain": chain_ok,
        "ok": ok.get("pass", False),
        "note": note,
        "bucket": bucket,
    }


class TempHome:
    """Isolate AITIME_HOME / STRUGGLETIME_* under a temp root."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "aitime_home"
        self.cfg = root / "config"
        self.agent_log = self.home / "agent_events.jsonl"
        self.exec_log = self.home / "exec.jsonl"
        self._old_env = {}
        self._old_cfg = None

    def __enter__(self):
        self.home.mkdir(parents=True)
        self.cfg.mkdir(parents=True)
        (self.cfg / "settings.json").write_text(json.dumps({
            "handle": "", "repo": "", "server": "http://127.0.0.1:9",
            "token": "", "bucket_mins": 5, "project": "", "projects": [],
        }) + "\n")
        keys = {
            "AITIME_HOME": str(self.home),
            "STRUGGLETIME_HOME": str(self.home),
            "AITIME_CONFIG": str(self.cfg),
            "STRUGGLETIME_CONFIG": str(self.cfg),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            "AITIME_AGENT_EVENTS": str(self.agent_log),
            "STRUGGLETIME_AGENT_EVENTS": str(self.agent_log),
            "AITIME_EXEC_LOG": str(self.exec_log),
            "STRUGGLETIME_EXEC_LOG": str(self.exec_log),
            "AITIME_SERVER": "http://127.0.0.1:9",
            "STRUGGLETIME_SERVER": "http://127.0.0.1:9",
            "AITIME_INSTALL": "matrix-test",
            "STRUGGLETIME_INSTALL": "matrix-test",
        }
        for k, v in keys.items():
            self._old_env[k] = os.environ.get(k)
            os.environ[k] = v
        # Clear attribution env unless a scenario sets them
        for k in ("AITIME_EDITOR", "STRUGGLETIME_EDITOR", "AITIME_AGENT", "STRUGGLETIME_AGENT"):
            self._old_env[k] = os.environ.get(k)
            os.environ.pop(k, None)
        import config as dcfg
        self._old_cfg = (
            dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS, dcfg.LEGACY_GUI_SETTINGS,
            dcfg.EDITORS_JSON, dcfg.PAUSE_FLAG, agent_evt.EVENT_LOG, agent_evt.EXEC_LOG,
        )
        dcfg.HOME = self.home
        dcfg.CONFIG_HOME = self.cfg
        dcfg.GUI_SETTINGS = self.cfg / "settings.json"
        dcfg.LEGACY_GUI_SETTINGS = self.home / "gui.json"
        dcfg.EDITORS_JSON = self.cfg / "editors.json"
        dcfg.PAUSE_FLAG = self.home / "paused"
        agent_evt.EVENT_LOG = self.agent_log
        agent_evt.EXEC_LOG = self.exec_log
        _clear_editor_cache()
        return self

    def __exit__(self, *exc):
        import config as dcfg
        if self._old_cfg:
            (dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS, dcfg.LEGACY_GUI_SETTINGS,
             dcfg.EDITORS_JSON, dcfg.PAUSE_FLAG, agent_evt.EVENT_LOG, agent_evt.EXEC_LOG) = self._old_cfg
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _clear_editor_cache()


def _append_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def _seal(w, bucket_secs=5):
    now = time.monotonic()
    if not w["started_mono"]:
        w["started_wall"] = int(time.time())
        w["started_mono"] = now - bucket_secs
        w["last_activity"] = now
    bucket = daemon_mod.seal_bucket(w, bucket_secs, "matrix-test")
    daemon_mod.save_ledger(w["ledger"], w["state"])
    return bucket


def _verify_bucket(repo, bucket, *, expect_struggle=True, expect_dump=False, want_editor=None, want_agent=None):
    buckets = [bucket]
    chain_errs, allow_errs, salt_leak = _inspect_ok(repo, buckets)
    w, state = verify.weight(bucket)
    ok = {
        "chain": not chain_errs,
        "allowlist": not allow_errs and not salt_leak,
        "pass": False,
    }
    reasons = []
    if chain_errs:
        reasons.append(f"chain:{chain_errs}")
    if allow_errs:
        reasons.append(f"allowlist:{allow_errs}")
    if salt_leak:
        reasons.append("salt_leak")
    if expect_struggle:
        if state not in STRUGGLE_STATES:
            reasons.append(f"state={state} not struggle")
        if w != 1.0:
            reasons.append(f"weight={w} != 1.0")
        # Prefer AI_STRUGGLE when counters present
        sc = _struggle_counters(bucket["counts"])
        if any(sc.values()) and state != "AI_STRUGGLE":
            # CLOSED_LOOP/STRUGGLE_LOOP acceptable only if is_ai_struggle false somehow
            if not verify.is_ai_struggle(bucket["counts"]):
                reasons.append(f"expected AI_STRUGGLE got {state}")
            elif state != "AI_STRUGGLE":
                reasons.append(f"prefer AI_STRUGGLE got {state}")
    if expect_dump:
        if state != "OPEN_LOOP_AGENT_DUMP":
            reasons.append(f"state={state} != OPEN_LOOP_AGENT_DUMP")
        if abs(w - 0.2) > 0.001:
            reasons.append(f"weight={w} != 0.2")
    eds = bucket.get("editors") or {}
    ags = bucket.get("agents") or {}
    if want_editor and want_editor not in eds and want_editor not in {
        f.get("editor") for f in bucket.get("files") or []
    }:
        # env/adapter attribution may land on files or editors aggregate
        if not any(want_editor == e for e in eds):
            reasons.append(f"missing editor={want_editor} got {eds}")
    if want_agent and want_agent not in ags and want_agent not in {
        f.get("agent") for f in bucket.get("files") or []
    }:
        if want_agent not in ags:
            reasons.append(f"missing agent={want_agent} got {ags}")
    ok["pass"] = not reasons
    ok["reasons"] = reasons
    return ok


def simulate_adapter_struggle(repo: Path, project_id: str, editor: str, agent: str, home: TempHome):
    """Scenario A/B: adapter/spool events — edit→fail→reedit→fail→human→pass."""
    src = repo / "app.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    w = daemon_mod.make_watcher(repo, project_id)
    now = int(time.time())
    pid = project_id

    events = [
        _evt(now, category="ai_cli", editor=editor, agent=agent, event_type="prompt", verb="prompt", project_id=pid),
        _evt(now, category="ai_cli", editor=editor, agent=agent, event_type="tool", verb="edit",
             files_touched_count=5, project_id=pid),
        _evt(now, category="test_runner", editor=editor, agent=agent, event_type="exec", verb="pytest",
             name="pytest", exit_code=1, project_id=pid),
        _evt(now + 1, category="ai_cli", editor=editor, agent=agent, event_type="tool", verb="edit",
             files_touched_count=1, project_id=pid),
        _evt(now + 1, category="test_runner", editor=editor, agent=agent, event_type="exec", verb="pytest",
             name="pytest", exit_code=1, project_id=pid),
        _evt(now + 2, category="editor", editor=editor, agent="human", event_type="edit", verb="edit",
             op="modified", files_touched_count=1, project_id=pid),
        _evt(now + 2, category="test_runner", editor=editor, agent=agent, event_type="exec", verb="pytest",
             name="pytest", exit_code=0, project_id=pid),
    ]
    # Spool to disk (adapter mode) then drain — also apply via classify for realism
    _append_jsonl(home.agent_log, events)
    shared = {"exec_offset": 0, "agent_offset": 0}
    daemon_mod.drain_shared_logs([w], home.agent_log, home.exec_log, shared)

    # File mtime so sealed bucket has anonymized file rows + attribution
    src.write_text("def add(a, b):\n    return a + b  # fixed\n")
    w["attr_hint"] = {"editor": editor, "agent": agent, "source": "adapter"}
    daemon_mod.poll_repo_files(w, 5)
    # Targeted human fix mtime
    os.environ["STRUGGLETIME_EDITOR"] = editor
    os.environ.pop("STRUGGLETIME_AGENT", None)
    os.environ.pop("AITIME_AGENT", None)
    _clear_editor_cache()
    time.sleep(0.05)
    src.write_text("def add(a, b):\n    return a + b\n\ndef test_add():\n    assert add(1, 1) == 2\n")
    w["attr_hint"] = {"editor": editor, "agent": "human", "source": "adapter"}
    daemon_mod.poll_repo_files(w, 5)

    bucket = _seal(w)
    return bucket, w


def simulate_session_struggle(repo: Path, project_id: str, editor: str, agent: str, home: TempHome):
    """Scenario B flavor: iterative CLI retries via exec.jsonl + file cycles."""
    src = repo / "mod.py"
    src.write_text("x = 0\n")
    w = daemon_mod.make_watcher(repo, project_id)
    now = int(time.time())
    # Session/exec loop: prompts + failing builds, then pass
    rows = [
        {"ts": now, "name": "codex" if agent == "codex" else agent, "category": "ai_cli",
         "verb": "run", "exit_code": 0, "editor": editor, "agent": agent,
         "event_type": "prompt", "project_id": project_id},
        {"ts": now, "name": "python", "category": "test_runner", "verb": "test",
         "exit_code": 1, "editor": editor, "agent": agent, "project_id": project_id},
        {"ts": now + 1, "event_type": "tool", "category": "ai_cli", "verb": "edit",
         "exit_code": 0, "editor": editor, "agent": agent,
         "files_touched_count": 5, "project_id": project_id},
        {"ts": now + 1, "name": "python", "category": "test_runner", "verb": "test",
         "exit_code": 1, "editor": editor, "agent": agent, "project_id": project_id},
        {"ts": now + 2, "event_type": "tool", "category": "ai_cli", "verb": "edit",
         "exit_code": 0, "editor": editor, "agent": agent,
         "files_touched_count": 1, "project_id": project_id},
        {"ts": now + 2, "name": "python", "category": "test_runner", "verb": "test",
         "exit_code": 1, "editor": editor, "agent": agent, "project_id": project_id},
        {"ts": now + 3, "event_type": "edit", "category": "editor", "verb": "edit",
         "op": "modified", "exit_code": 0, "editor": editor, "agent": "human",
         "files_touched_count": 1, "project_id": project_id},
        {"ts": now + 3, "name": "python", "category": "test_runner", "verb": "test",
         "exit_code": 0, "editor": editor, "agent": agent, "project_id": project_id},
    ]
    _append_jsonl(home.exec_log, rows)
    shared = {"exec_offset": 0, "agent_offset": 0}
    daemon_mod.drain_shared_logs([w], home.agent_log, home.exec_log, shared)

    for i in range(3):
        src.write_text(f"x = {i}\n")
        w["attr_hint"] = {"editor": editor, "agent": agent, "source": "adapter"}
        daemon_mod.poll_repo_files(w, 5)
        time.sleep(0.02)
    src.write_text("x = 42\n")
    w["attr_hint"] = {"editor": editor, "agent": "human", "source": "adapter"}
    daemon_mod.poll_repo_files(w, 5)

    bucket = _seal(w)
    return bucket, w


def simulate_passive_struggle(repo: Path, project_id: str, editor: str, agent: str):
    """Scenario C: pure passive — empty adapter logs; env attribution + mtime + fail/pass."""
    os.environ["STRUGGLETIME_EDITOR"] = editor
    os.environ["AITIME_EDITOR"] = editor
    os.environ["STRUGGLETIME_AGENT"] = agent
    os.environ["AITIME_AGENT"] = agent
    _clear_editor_cache()
    # Isolate from host IDE processes (this machine often has Cursor running)
    edreg._CACHE["blob"] = f"{editor}\n"
    edreg._CACHE["blob_at"] = time.monotonic()

    target = repo / "work.py"
    target.write_text("def f():\n    return 0\n")
    w = daemon_mod.make_watcher(repo, project_id)
    w["attr_hint"] = {}  # force layer1/env path

    # Multi-tick same-file rewrites (AI via env agent)
    for i in range(4):
        target.write_text(f"def f():\n    return {i}\n")
        daemon_mod.poll_repo_files(w, 5)
        time.sleep(0.02)

    now = int(time.time())
    # Fail then pass without adapter JSONL — inject cleaned events (shell-hook equivalent)
    for exit_code in (1, 1, 0):
        e = {
            "category": "test_runner", "event_type": "exec", "verb": "pytest",
            "exit_code": exit_code, "ts": now, "source": "layer1",
            "editor": None, "agent": None, "op": None, "files_touched_count": 0,
            "project_id": None, "_cwd": str(repo),
        }
        daemon_mod.apply_event_to_watcher(w, e, now, time.monotonic())
        if exit_code == 1:
            # re-edit after fail
            target.write_text(f"def f():\n    return {exit_code + 10}\n")
            daemon_mod.poll_repo_files(w, 5)
            time.sleep(0.02)

    # Human recovery: hint overrides env; keep editor env for attribution
    w["attr_hint"] = {"editor": editor, "agent": "human", "source": "layer1"}
    target.write_text("def f():\n    return 1\n")
    # Force human kind path: empty running agents for this tick only
    prev_blob = edreg._CACHE.get("blob")
    edreg._CACHE["blob"] = "bash\n"
    edreg._CACHE["blob_at"] = time.monotonic()
    os.environ.pop("STRUGGLETIME_AGENT", None)
    os.environ.pop("AITIME_AGENT", None)
    daemon_mod.poll_repo_files(w, 5)
    edreg._CACHE["blob"] = prev_blob
    edreg._CACHE["blob_at"] = time.monotonic()

    bucket = _seal(w)
    return bucket, w


def simulate_bulk_dump(repo: Path, project_id: str):
    """Scenario D: single-tick 6+ file AI dump, no tests, no reedits."""
    for k in ("STRUGGLETIME_EDITOR", "AITIME_EDITOR", "STRUGGLETIME_AGENT", "AITIME_AGENT"):
        os.environ.pop(k, None)
    _clear_editor_cache()
    # No live agent noise — pure bulk dump control
    edreg._CACHE["blob"] = "bash\n"
    edreg._CACHE["blob_at"] = time.monotonic()

    w = daemon_mod.make_watcher(repo, project_id)
    # One poll creates 6 files with enough lines for AI bulk classification
    for i in range(6):
        body = "\n".join(f"# line {j} of file {i}" for j in range(40))
        (repo / f"gen_{i}.py").write_text(body + f"\ndef f{i}():\n    return {i}\n")
    daemon_mod.poll_repo_files(w, 5)
    bucket = _seal(w)
    return bucket, w


def collect_matrix(base: Path | None = None):
    """Run full A/B/C/D matrix; return (rows, overall_pass). Cleans temp tree."""
    root = Path(base or tempfile.mkdtemp(prefix="aitime_struggle_matrix_", dir="/tmp"))
    rows = []
    overall = True
    try:
        with TempHome(root / "home") as home:
            # --- Scenario A ---
            for editor, agent in (("claude-code", "claude"), ("opencode", "opencode")):
                repo = root / f"a_{agent}"
                repo.mkdir(parents=True)
                _git_init(repo)
                # fresh logs per case
                home.agent_log.write_text("")
                home.exec_log.write_text("")
                bucket, _ = simulate_adapter_struggle(repo, f"proj-a-{agent}", editor, agent, home)
                ok = _verify_bucket(repo, bucket, expect_struggle=True, want_editor=editor, want_agent=agent)
                if not ok["pass"]:
                    overall = False
                rows.append(_row("A", f"{agent}/{editor}", "adapter/spool", bucket, ok,
                                 note=";".join(ok.get("reasons") or [])))

            # --- Scenario B ---
            for editor, agent in (("openclaw", "openclaw"), ("codex", "codex")):
                repo = root / f"b_{agent}"
                repo.mkdir(parents=True)
                _git_init(repo)
                home.agent_log.write_text("")
                home.exec_log.write_text("")
                bucket, _ = simulate_session_struggle(repo, f"proj-b-{agent}", editor, agent, home)
                ok = _verify_bucket(repo, bucket, expect_struggle=True, want_editor=editor, want_agent=agent)
                if not ok["pass"]:
                    overall = False
                rows.append(_row("B", f"{agent}/{editor}", "session/exec", bucket, ok,
                                 note=";".join(ok.get("reasons") or [])))

            # --- Scenario C ---
            passive = [
                ("cursor", "cursor-agent"),
                ("windsurf", "windsurf-agent"),
                ("zed", "zed-agent"),
                ("neovim", "mystery-agent"),
            ]
            for editor, agent in passive:
                repo = root / f"c_{editor}"
                repo.mkdir(parents=True)
                _git_init(repo)
                home.agent_log.write_text("")
                home.exec_log.write_text("")
                bucket, _ = simulate_passive_struggle(repo, f"proj-c-{editor}", editor, agent)
                ok = _verify_bucket(repo, bucket, expect_struggle=True, want_editor=editor, want_agent=agent)
                if not ok["pass"]:
                    overall = False
                rows.append(_row("C", f"{agent}/{editor}", "passive", bucket, ok,
                                 note=";".join(ok.get("reasons") or [])))

            # --- Scenario D ---
            repo = root / "d_dump"
            repo.mkdir(parents=True)
            _git_init(repo)
            home.agent_log.write_text("")
            home.exec_log.write_text("")
            bucket, _ = simulate_bulk_dump(repo, "proj-d-dump")
            ok = _verify_bucket(repo, bucket, expect_struggle=False, expect_dump=True)
            if not ok["pass"]:
                overall = False
            rows.append(_row("D", "bulk-dump/n/a", "control", bucket, ok,
                             note=";".join(ok.get("reasons") or [])))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return rows, overall


def format_table(rows):
    lines = [
        "| Scenario | Agent/Editor | Mode | State | Weight | Struggle counters | Allowlist | Chain |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        sc = r["counters"]
        ctr = (
            f"reedit={sc['same_file_reedit_count']} retry={sc['ai_retry_loops']} "
            f"fail={sc['failed_exec_count']} f2p={sc['test_fail_to_pass_transitions']} "
            f"churn={sc['agent_turn_churn']} recovery={sc['human_recovery_edits']}"
        )
        label = r["label"]
        mark = "PASS" if r["ok"] else "FAIL"
        lines.append(
            f"| {r['scenario']} ({mark}) | {label} | {r['mode']} | {r['state']} | "
            f"{r['weight']} | {ctr} | {r['allowlist']} | {r['chain']} |"
        )
    return "\n".join(lines)


class TestAIStruggleMatrix(unittest.TestCase):
    def test_full_matrix(self):
        rows, overall = collect_matrix()
        failed = [r for r in rows if not r["ok"]]
        msg = format_table(rows)
        if failed:
            details = "\n".join(f"{r['scenario']} {r['label']}: {r['note']} state={r['state']}" for r in failed)
            self.fail(f"matrix failures:\n{details}\n\n{msg}")
        self.assertTrue(overall)
        # Spot-check: A prefers AI_STRUGGLE; D is dump
        a_rows = [r for r in rows if r["scenario"] == "A"]
        self.assertTrue(a_rows)
        for r in a_rows:
            self.assertEqual(r["state"], "AI_STRUGGLE")
            self.assertEqual(r["weight"], 1.0)
        d = [r for r in rows if r["scenario"] == "D"][0]
        self.assertEqual(d["state"], "OPEN_LOOP_AGENT_DUMP")
        self.assertAlmostEqual(d["weight"], 0.2)

    def test_adapter_claude_subprocess_once(self):
        """One e2e path: write spools + daemon.py --once against temp repo."""
        root = Path(tempfile.mkdtemp(prefix="aitime_once_", dir="/tmp"))
        try:
            with TempHome(root / "home") as home:
                repo = root / "repo"
                repo.mkdir()
                _git_init(repo)
                src = repo / "app.py"
                src.write_text("def add(a, b):\n    return a\n")
                now = int(time.time())
                events = [
                    _evt(now, category="ai_cli", editor="claude-code", agent="claude",
                         event_type="prompt", verb="prompt", project_id="once-claude"),
                    _evt(now, category="ai_cli", editor="claude-code", agent="claude",
                         event_type="tool", verb="edit", files_touched_count=5, project_id="once-claude"),
                    _evt(now, category="test_runner", editor="claude-code", agent="claude",
                         event_type="exec", verb="pytest", exit_code=1, project_id="once-claude"),
                    _evt(now, category="ai_cli", editor="claude-code", agent="claude",
                         event_type="tool", verb="edit", files_touched_count=1, project_id="once-claude"),
                    _evt(now, category="test_runner", editor="claude-code", agent="claude",
                         event_type="exec", verb="pytest", exit_code=1, project_id="once-claude"),
                    _evt(now, category="editor", editor="claude-code", agent="human",
                         event_type="edit", verb="edit", op="modified",
                         files_touched_count=1, project_id="once-claude"),
                    _evt(now, category="test_runner", editor="claude-code", agent="claude",
                         event_type="exec", verb="pytest", exit_code=0, project_id="once-claude"),
                ]
                _append_jsonl(home.agent_log, events)
                src.write_text("def add(a, b):\n    return a + b\n")
                env = os.environ.copy()
                env["AITIME_BUCKET_SECS"] = "2"
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "daemon" / "daemon.py"),
                     "--repo", str(repo), "--project", "once-claude",
                     "--bucket-secs", "2", "--once",
                     "--server", "http://127.0.0.1:9"],
                    env=env, capture_output=True, text=True, timeout=30,
                )
                ledger_path = repo / ".aitime-ledger.json"
                self.assertTrue(ledger_path.exists(), proc.stderr + proc.stdout)
                pending = json.loads(ledger_path.read_text()).get("pending") or []
                self.assertTrue(pending, proc.stdout + proc.stderr)
                bucket = pending[-1]
                w, state = verify.weight(bucket)
                self.assertEqual(verify.validate_allowlist(bucket), [])
                self.assertEqual(verify.verify_chain(pending), [])
                self.assertIn(state, STRUGGLE_STATES)
                self.assertEqual(w, 1.0)
                self.assertIn("claude", bucket.get("agents") or {})
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    rows, overall = collect_matrix()
    print(format_table(rows))
    print()
    print("**PASS**" if overall else "**FAIL**", f"({sum(1 for r in rows if r['ok'])}/{len(rows)} scenarios)")
    # Also run unittest for the subprocess check when invoked directly with -v
    if "--unittest" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--unittest"]
        unittest.main()
    else:
        sys.exit(0 if overall else 1)
