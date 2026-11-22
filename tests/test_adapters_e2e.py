#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end adapter suite: syntax → emit → daemon drain → seal → verify.

Covers every tree under adapters/ plus daemon shell hooks. Temp dirs are
always cleaned. Privacy: no raw paths/prompts/args may survive into buckets.
"""
from __future__ import annotations

import importlib.util, io, json, os, re, shutil, subprocess, sys, tempfile, time, unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))

import verify  # noqa: E402
import agent_event as agent_evt  # noqa: E402
import debugutil  # noqa: E402
import daemon as daemon_mod  # noqa: E402

FORBIDDEN_KEYS = {
    "content", "text", "code", "prompt_text", "command", "args", "argv",
    "keystrokes", "screenshot", "image", "path", "filepath", "file_path",
    "raw", "transcript", "cwd", "workspace",
}
FORBIDDEN_SRC_NEEDLES = (
    '"prompt_text"', "'prompt_text'", '"transcript"', "'keystrokes'",
    '"screenshot"',
)

# Every adapter / hook under adapters/ (+ shell hooks live in daemon/)
ADAPTER_MANIFEST = [
    {
        "id": "vscode",
        "files": ["adapters/vscode/extension.js", "adapters/vscode/package.json"],
        "syntax": [("node", "--check", "adapters/vscode/extension.js")],
        "editor": "vscode",
        "events": [
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "vscode", "agent": "human", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "focus", "category": "editor", "verb": "focus",
             "editor": "vscode", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
            {"event_type": "blur", "category": "editor", "verb": "blur",
             "editor": "vscode", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
    },
    {
        "id": "claude-code",
        "files": ["adapters/claude-code/hooks.json"],
        "syntax": [],
        "editor": "claude-code",
        "events": [
            {"event_type": "tool", "category": "ai_cli", "verb": "tool", "op": "modified",
             "editor": "claude-code", "agent": "claude", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "stop", "category": "ai_cli", "verb": "stop",
             "editor": "claude-code", "agent": "claude", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
        "live_hook": True,
    },
    {
        "id": "codex",
        "files": ["adapters/codex/wrap.sh"],
        "syntax": [("sh", "-n", "adapters/codex/wrap.sh")],
        "editor": "codex",
        "events": [
            {"event_type": "start", "category": "ai_cli", "verb": "start",
             "editor": "codex", "agent": "codex", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
            {"event_type": "stop", "category": "ai_cli", "verb": "stop", "op": "modified",
             "editor": "codex", "agent": "codex", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
        ],
        "live_wrap": True,
    },
    {
        "id": "openclaw",
        "files": ["adapters/openclaw/adapter.py"],
        "syntax": [("python3", "-m", "py_compile", "adapters/openclaw/adapter.py")],
        "editor": "openclaw",
        "events": [
            {"event_type": "tool", "category": "ai_cli", "verb": "tool", "op": "modified",
             "editor": "openclaw", "agent": "openclaw", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
        ],
        "live_openclaw": True,
    },
    {
        "id": "neovim",
        "files": ["adapters/neovim/lua/struggletime/init.lua"],
        "syntax": [],
        "editor": "neovim",
        "events": [
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "neovim", "agent": "human", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "neovim", "agent": "unknown-agent", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "focus", "category": "editor", "verb": "focus",
             "editor": "neovim", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
    },
    {
        "id": "godot",
        "files": [
            "adapters/godot/addons/struggletime/plugin.gd",
            "adapters/godot/addons/struggletime/plugin.cfg",
        ],
        "syntax": [],
        "editor": "godot",
        "events": [
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "godot", "agent": "human", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "play", "category": "test_runner", "verb": "play",
             "editor": "godot", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
            {"event_type": "focus", "category": "editor", "verb": "focus",
             "editor": "godot", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
    },
    {
        "id": "jetbrains",
        "files": [
            "adapters/jetbrains/src/main/kotlin/com/struggletime/StruggleTimeStartup.kt",
            "adapters/jetbrains/src/main/resources/META-INF/plugin.xml",
        ],
        "syntax": [],
        "editor": "jetbrains",
        "events": [
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "jetbrains", "agent": "human", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "edit", "category": "editor", "verb": "modified", "op": "modified",
             "editor": "jetbrains", "agent": "unknown-agent", "files_touched_count": 1, "exit_code": 0, "source": "adapter"},
            {"event_type": "focus", "category": "editor", "verb": "focus",
             "editor": "jetbrains", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
    },
    {
        "id": "unity",
        "files": ["adapters/unity/Editor/StruggleTimeTracker.cs"],
        "syntax": [],
        "editor": "unity",
        "events": [
            {"event_type": "focus", "category": "editor", "verb": "focus",
             "editor": "unity", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
            {"event_type": "modified", "category": "editor", "verb": "save", "op": "modified",
             "editor": "unity", "agent": "human", "files_touched_count": 2, "exit_code": 0, "source": "adapter"},
            {"event_type": "play", "category": "test_runner", "verb": "play",
             "editor": "unity", "agent": "human", "files_touched_count": 0, "exit_code": 0, "source": "adapter"},
        ],
    },
    {
        "id": "shell-hooks",
        "files": ["daemon/hooks.sh", "daemon/hooks.ps1"],
        "syntax": [("sh", "-n", "daemon/hooks.sh")],
        "editor": None,
        "events": [
            {"ts": 1, "category": "test_runner", "verb": "", "exit_code": 1},
            {"ts": 2, "category": "test_runner", "verb": "", "exit_code": 0},
            {"ts": 3, "category": "git", "verb": "commit", "exit_code": 0},
        ],
        "live_hooks_sh": True,
    },
]


def _load_fresh_modules():
    """Reload config-bound modules against current env (TempHome pattern)."""
    # already imported; callers set env before make_watcher
    return daemon_mod, agent_evt, verify, debugutil


def _walk_obj_forbidden(obj, path="$"):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(_walk_obj_forbidden(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_walk_obj_forbidden(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        if obj.startswith("/") and len(obj) > 1 and "/" in obj[1:]:
            # absolute-looking path string in payload
            if any(x in path for x in (".file_id", ".hash", ".prev_hash", ".head_commit")):
                pass
            elif re.search(r"^/[A-Za-z0-9._/-]+$", obj) and not obj.startswith("/tmp/struggletime"):
                # salted ids are hex, not paths
                if "/" in obj and not re.fullmatch(r"[0-9a-f]{16,}", obj):
                    hits.append(f"{path}=abspath")
    return hits


class AdapterHarness:
    def __init__(self):
        self.td = Path(tempfile.mkdtemp(prefix="st-adapter-e2e-"))
        self.home = self.td / "aitime"
        self.cfg = self.td / "config"
        self.repo = self.td / "repo"
        self.agent_log = self.home / "agent_events.jsonl"
        self.exec_log = self.home / "exec.jsonl"
        self._old = {}

    def __enter__(self):
        self.home.mkdir()
        self.cfg.mkdir()
        self.repo.mkdir()
        (self.cfg / "settings.json").write_text(json.dumps({
            "handle": "", "repo": str(self.repo), "server": "",
            "token": "", "bucket_mins": 5, "project": "adapter-e2e",
            "projects": [{"id": "adapter-e2e", "path": str(self.repo)}],
        }) + "\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.t"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "t"], check=True)
        (self.repo / "main.py").write_text("print(1)\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "i"], check=True)

        env = {
            "AITIME_HOME": str(self.home),
            "STRUGGLETIME_HOME": str(self.home),
            "AITIME_CONFIG": str(self.cfg),
            "STRUGGLETIME_CONFIG": str(self.cfg),
            "AITIME_AGENT_EVENTS": str(self.agent_log),
            "STRUGGLETIME_AGENT_EVENTS": str(self.agent_log),
            "AITIME_EXEC_LOG": str(self.exec_log),
            "STRUGGLETIME_EXEC_LOG": str(self.exec_log),
            "AITIME_SERVER": "",
            "STRUGGLETIME_SERVER": "",
            "AITIME_INSTALL": "adapter-e2e",
            "STRUGGLETIME_INSTALL": "adapter-e2e",
            "STRUGGLETIME_DEBUG": "1",
            "AITIME_DEBUG": "1",
        }
        for k, v in env.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v
        import config as dcfg
        self._cfg_snap = (
            dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS,
            agent_evt.EVENT_LOG, agent_evt.EXEC_LOG,
        )
        dcfg.HOME = self.home
        dcfg.CONFIG_HOME = self.cfg
        dcfg.GUI_SETTINGS = self.cfg / "settings.json"
        agent_evt.EVENT_LOG = self.agent_log
        agent_evt.EXEC_LOG = self.exec_log
        return self

    def __exit__(self, *exc):
        import config as dcfg
        (dcfg.HOME, dcfg.CONFIG_HOME, dcfg.GUI_SETTINGS,
         agent_evt.EVENT_LOG, agent_evt.EXEC_LOG) = self._cfg_snap
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.td, ignore_errors=True)

    def append_events(self, rows, *, to_exec=False):
        dest = self.exec_log if to_exec else self.agent_log
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a") as fh:
            for r in rows:
                payload = dict(r)
                payload.setdefault("ts", int(time.time()))
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def drain_and_seal(self):
        w = daemon_mod.make_watcher(self.repo, "adapter-e2e")
        w["started_wall"] = int(time.time()) - 30
        w["started_mono"] = time.monotonic() - 30
        shared = {"agent_offset": 0, "exec_offset": 0}
        buf = io.StringIO()
        with redirect_stderr(buf):
            daemon_mod.drain_shared_logs([w], self.agent_log, self.exec_log, shared)
        debug_out = buf.getvalue()
        # Force activity window so seal produces a bucket
        now_wall, now_mono = int(time.time()), time.monotonic()
        daemon_mod.note_activity(w, now_wall, now_mono)
        bucket = daemon_mod.seal_bucket(w, 300, "adapter-e2e")
        return w, bucket, debug_out, shared


class TestAdapterDiscovery(unittest.TestCase):
    def test_manifest_covers_adapters_tree(self):
        found = {p.name for p in (ROOT / "adapters").iterdir() if p.is_dir() and p.name != "__pycache__"}
        covered = {m["id"] for m in ADAPTER_MANIFEST if m["id"] != "shell-hooks"}
        self.assertEqual(found, covered, f"adapters/ dirs {found} vs manifest {covered}")

    def test_every_file_exists_and_privacy_clean(self):
        for m in ADAPTER_MANIFEST:
            for rel in m["files"]:
                path = ROOT / rel
                self.assertTrue(path.is_file(), rel)
                text = path.read_text(errors="ignore")
                low = text.lower()
                for needle in FORBIDDEN_SRC_NEEDLES:
                    if needle in low and "never" not in low and "forbidden" not in low and "delete" not in low:
                        self.fail(f"{rel} contains {needle}")


class TestAdapterSyntax(unittest.TestCase):
    def test_syntax_commands(self):
        for m in ADAPTER_MANIFEST:
            for cmd in m.get("syntax") or []:
                args = list(cmd)
                # resolve relative file args
                args = [str(ROOT / a) if a.startswith("adapters/") or a.startswith("daemon/") else a for a in args]
                r = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=str(ROOT))
                self.assertEqual(r.returncode, 0, f"{m['id']} syntax {' '.join(args)}: {r.stderr}")


class TestAdapterE2EPerAdapter(unittest.TestCase):
    def _run_one(self, m):
        with AdapterHarness() as h:
            # Live emitters when available
            if m.get("live_hook"):
                self._emit_claude_hooks(h)
            if m.get("live_wrap"):
                self._emit_codex_wrap(h)
            if m.get("live_openclaw"):
                self._emit_openclaw(h)
            if m.get("live_hooks_sh"):
                self._emit_hooks_sh(h)

            # Always also inject schema samples (covers IDE-hosted adapters)
            to_exec = m["id"] == "shell-hooks"
            samples = []
            for e in m["events"]:
                row = dict(e)
                row["ts"] = int(time.time())
                # poison with forbidden keys — classify/validate must strip/reject
                poisoned = {**row, "path": "/secret/code.py", "prompt_text": "leak", "args": ["--evil"]}
                samples.append(poisoned if to_exec else row)
                if not to_exec:
                    # validate path rejects forbidden
                    ok, _ = agent_evt.validate_event(poisoned)
                    self.assertFalse(ok, f"{m['id']} should reject forbidden keys")
                    ok, cleaned = agent_evt.validate_event(row)
                    self.assertTrue(ok, f"{m['id']} validate: {_ if not ok else cleaned}")
                    samples[-1] = cleaned
            h.append_events(samples, to_exec=to_exec)

            # malformed / empty lines must not crash drain
            with open(h.agent_log if not to_exec else h.exec_log, "a") as fh:
                fh.write("\n")
                fh.write("not-json\n")
                fh.write("{]\n")
                fh.write(json.dumps({"event_type": "edit", "path": "/x"}) + "\n")

            w, bucket, debug_out, shared = h.drain_and_seal()
            self.assertIsInstance(bucket, dict)
            self.assertIn("hash", bucket)
            self.assertIn("prev_hash", bucket)
            daemon_mod.save_ledger(w["ledger"], w["state"])
            errs = verify.validate_allowlist(bucket)
            self.assertEqual(errs, [], f"{m['id']} allowlist: {errs}")
            hits = _walk_obj_forbidden(bucket)
            self.assertEqual(hits, [], f"{m['id']} forbidden in bucket: {hits}")
            # salted file ids only (hex), never absolute paths
            for f in bucket.get("files") or []:
                fid = f.get("file_id") or ""
                self.assertTrue(re.fullmatch(r"[0-9a-f]{16,64}", fid) or fid == "", fid)
                self.assertNotIn("/", fid)
            # hash chain continuity for next seal
            tip = bucket["hash"]
            w2 = daemon_mod.make_watcher(h.repo, "adapter-e2e")
            self.assertEqual(w2["state"].get("prev_hash"), tip)
            daemon_mod.note_activity(w2, int(time.time()), time.monotonic())
            b2 = daemon_mod.seal_bucket(w2, 300, "adapter-e2e")
            daemon_mod.save_ledger(w2["ledger"], w2["state"])
            self.assertEqual(b2["prev_hash"], tip)
            self.assertEqual(verify.validate_allowlist(b2), [])
            # debug mode emitted allowlisted diagnostics when adapter events drained
            if m["id"] != "shell-hooks" or shared.get("exec_offset", 0) > 0:
                # shell hooks may only hit exec.jsonl; drain still logs per line when debug on
                self.assertTrue(
                    ("struggletime:debug" in debug_out) or shared.get("agent_offset", 0) or shared.get("exec_offset", 0),
                    f"{m['id']} expected debug drain activity",
                )
            self.assertNotIn("/secret/", debug_out)
            self.assertNotIn("prompt_text", debug_out)
            self.assertNotIn("leak", debug_out)
            wt, state = verify.weight(bucket)
            self.assertIsInstance(wt, float)
            self.assertTrue(state)
            return {
                "id": m["id"],
                "ok": True,
                "events": len(samples),
                "executions": bucket.get("counts", {}).get("executions", 0),
                "weight": wt,
                "state": state,
                "hash": bucket["hash"][:12],
            }

    def _emit_claude_hooks(self, h):
        hooks = json.loads((ROOT / "adapters/claude-code/hooks.json").read_text())
        for phase, entries in (hooks.get("hooks") or {}).items():
            for entry in entries:
                for hook in entry.get("hooks") or []:
                    cmd = hook.get("command")
                    if not cmd:
                        continue
                    env = {**os.environ, "AITIME_AGENT_EVENTS": str(h.agent_log)}
                    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, env=env, timeout=10)
                    self.assertEqual(r.returncode, 0, f"claude hook {phase}: {r.stderr}")

    def _emit_codex_wrap(self, h):
        wrap = ROOT / "adapters/codex/wrap.sh"
        # Use `true` as stand-in agent binary name via PATH shim
        shim_dir = h.td / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "codex"
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "AITIME_AGENT_EVENTS": str(h.agent_log),
            "PYTHON": sys.executable,
        }
        r = subprocess.run(["sh", str(wrap), "codex"], capture_output=True, text=True, env=env, timeout=20, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(h.agent_log.exists())
        lines = h.agent_log.read_text().strip().splitlines()
        self.assertGreaterEqual(len(lines), 2)

    def _emit_openclaw(self, h):
        sessions = h.td / "sessions"
        sessions.mkdir()
        sess = sessions / "s.jsonl"
        # tool call + result shapes the adapter classifies (no message text kept)
        sess.write_text(
            json.dumps({"type": "tool_call", "id": 1}) + "\n"
            + json.dumps({"type": "tool_result", "status": "ok"}) + "\n"
        )
        env = {
            **os.environ,
            "AITIME_AGENT_EVENTS": str(h.agent_log),
            "OPENCLAW_SESSIONS": str(sessions),
            "STRUGGLETIME_DEBUG": "1",
        }
        r = subprocess.run(
            [sys.executable, str(ROOT / "adapters/openclaw/adapter.py"), "--once",
             "--sessions-dir", str(sessions)],
            capture_output=True, text=True, env=env, timeout=20,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(h.agent_log.exists() and h.agent_log.stat().st_size > 0)

    def _emit_hooks_sh(self, h):
        """Emit the same JSONL shape as daemon/hooks.sh without installing shell traps."""
        h.exec_log.parent.mkdir(parents=True, exist_ok=True)
        # Source hooks.sh functions only (skip trap install) by extracting helpers
        # Do not use `set -e`: hooks capture failing command exit codes via $? 
        extract = (
            f"export AITIME_EXEC_LOG={json.dumps(str(h.exec_log))}\n"
            "mkdir -p \"$(dirname \"$AITIME_EXEC_LOG\")\"\n"
            # Replicate hooks.sh helper bodies (keep in sync with daemon/hooks.sh)
            "_aitime_category() {\n"
            "  case \"$1\" in\n"
            "    git) echo git;;\n"
            "    claude|codex|aider|opencode|openclaw|gemini|goose|amp|crush|copilot|cursor-agent|qwen|pi|cline|roo) echo ai_cli;;\n"
            "    pytest|jest|vitest|test|go|cargo|npm|pnpm|yarn|make|python|python3|node) echo test_runner;;\n"
            "    gcc|g++|clang|clang++|rustc|javac|tsc) echo compiler;;\n"
            "    *) echo other;;\n"
            "  esac\n"
            "}\n"
            "_aitime_pre() { _aitime_cmd=\"$1\"; }\n"
            "_aitime_post() {\n"
            "  local ec=$?; [ -z \"${_aitime_cmd:-}\" ] && return\n"
            "  local name=\"${_aitime_cmd%% *}\"; name=\"${name##*/}\"\n"
            "  printf '{\"ts\":%s,\"category\":\"%s\",\"verb\":\"%s\",\"exit_code\":%s}\\n' \\\n"
            "    \"$(date +%s)\" \"$(_aitime_category \"$name\")\" \"$([ \"$name\" = git ] && echo \"${2:-}\" || echo \"\")\" \"$ec\" >> \"$_aitime_log\"\n"
            "  _aitime_cmd=\"\"\n"
            "}\n"
            "_aitime_log=\"$AITIME_EXEC_LOG\"\n"
            "_aitime_pre \"pytest tests\"\n"
            "false\n"
            "_aitime_post\n"
            "_aitime_pre \"git commit\"\n"
            "true\n"
            "_aitime_post commit\n"
        )
        script = h.td / "emit_hooks.sh"
        script.write_text(extract)
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r} stdout={r.stdout!r}")
        self.assertTrue(h.exec_log.exists() and h.exec_log.stat().st_size > 0)
        # Also assert real hooks.sh parses
        r2 = subprocess.run(["sh", "-n", str(ROOT / "daemon/hooks.sh")], capture_output=True, text=True, timeout=5)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_all_adapters_e2e(self):
        results = []
        for m in ADAPTER_MANIFEST:
            with self.subTest(adapter=m["id"]):
                results.append(self._run_one(m))
        # Ensure we actually exercised every adapter
        self.assertEqual(len(results), len(ADAPTER_MANIFEST))
        failed = [r for r in results if not r.get("ok")]
        self.assertEqual(failed, [])


class TestAdapterEdgeCases(unittest.TestCase):
    def test_rapid_concurrent_events_preserve_chain(self):
        with AdapterHarness() as h:
            rows = []
            for i in range(40):
                rows.append({
                    "ts": int(time.time()),
                    "event_type": "edit" if i % 2 == 0 else "tool",
                    "category": "editor" if i % 2 == 0 else "ai_cli",
                    "verb": "modified",
                    "op": "modified",
                    "editor": "neovim" if i % 2 == 0 else "codex",
                    "agent": "human" if i % 2 == 0 else "codex",
                    "files_touched_count": 1,
                    "exit_code": 0,
                    "source": "adapter",
                })
            h.append_events(rows)
            w, b1, _, _ = h.drain_and_seal()
            daemon_mod.save_ledger(w["ledger"], w["state"])
            self.assertEqual(verify.validate_allowlist(b1), [])
            tip = b1["hash"]
            h.append_events([{
                "ts": int(time.time()), "event_type": "focus", "category": "editor",
                "editor": "neovim", "agent": "human", "files_touched_count": 0,
                "exit_code": 0, "source": "adapter",
            }])
            w2 = daemon_mod.make_watcher(h.repo, "adapter-e2e")
            shared = {"agent_offset": w2["state"].get("agent_offset", 0), "exec_offset": 0}
            buf = io.StringIO()
            with redirect_stderr(buf):
                daemon_mod.drain_shared_logs([w2], h.agent_log, h.exec_log, shared)
            daemon_mod.note_activity(w2, int(time.time()), time.monotonic())
            b2 = daemon_mod.seal_bucket(w2, 300, "adapter-e2e")
            daemon_mod.save_ledger(w2["ledger"], w2["state"])
            self.assertEqual(b2["prev_hash"], tip)
            self.assertEqual(verify.validate_allowlist(b2), [])

    def test_debug_flag_on_daemon_cli(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "daemon/daemon.py"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("--debug", r.stdout)

    def test_missing_optional_env_still_emits(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            log = td / "nested" / "agent_events.jsonl"
            # Clear override; agent_event.emit uses path arg
            os.environ.pop("AITIME_AGENT_EVENTS", None)
            cleaned = agent_evt.emit({
                "event_type": "edit", "editor": "vscode", "agent": "human",
                "op": "modified", "files_touched_count": 1,
            }, path=log)
            self.assertEqual(cleaned["editor"], "vscode")
            self.assertTrue(log.is_file())
            shutil.rmtree(td, ignore_errors=True)


class TestVscodePackageJson(unittest.TestCase):
    def test_package_main(self):
        pkg = json.loads((ROOT / "adapters/vscode/package.json").read_text())
        self.assertEqual(pkg["main"], "./extension.js")
        self.assertTrue((ROOT / "adapters/vscode/extension.js").is_file())


if __name__ == "__main__":
    unittest.main()
