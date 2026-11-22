#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Discover and verify every adapter under adapters/ (+ shell hooks).

Runs:
  1. Static discovery / privacy scan / syntax checks
  2. Full end-to-end suite: tests.test_adapters_e2e

Exit 0 on success. Stdlib only. Leaves no temp artifacts (tests clean up).
"""
from __future__ import annotations

import importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def discover():
    adapters = sorted(
        p.name for p in (ROOT / "adapters").iterdir()
        if p.is_dir() and p.name != "__pycache__"
    )
    hooks = []
    for rel in ("daemon/hooks.sh", "daemon/hooks.ps1"):
        if (ROOT / rel).is_file():
            hooks.append(rel)
    return adapters, hooks


def static_checks(errors):
    old_repo = "hacka" + "time-next"
    agent_event = load("agent_event_v", "daemon/agent_event.py")
    daemon = load("daemon_v", "daemon/daemon.py")
    debugutil = load("debugutil_v", "daemon/debugutil.py")
    editors = load("editors_v", "daemon/editors.py")

    adapters, hooks = discover()
    print(f"discovered adapters/: {', '.join(adapters)}")
    print(f"discovered hooks: {', '.join(hooks)}")

    required = {
        "vscode": ["adapters/vscode/extension.js", "adapters/vscode/package.json"],
        "claude-code": ["adapters/claude-code/hooks.json"],
        "codex": ["adapters/codex/wrap.sh"],
        "openclaw": ["adapters/openclaw/adapter.py"],
        "neovim": ["adapters/neovim/lua/struggletime/init.lua"],
        "godot": ["adapters/godot/addons/struggletime/plugin.gd"],
        "jetbrains": ["adapters/jetbrains/src/main/kotlin/com/struggletime/StruggleTimeStartup.kt"],
        "unity": ["adapters/unity/Editor/StruggleTimeTracker.cs"],
    }
    if set(adapters) != set(required):
        errors.append(f"adapters/ mismatch: found={adapters} expected={sorted(required)}")

    for name, files in required.items():
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                errors.append(f"missing {rel}")
                continue
            text = path.read_text(errors="ignore")
            for bad in ('"prompt_text"', '"transcript"', '"keystrokes"'):
                if bad in text.lower() and "never" not in text.lower() and "forbidden" not in text.lower():
                    errors.append(f"{rel} references {bad}")

    # syntax
    checks = [
        ["node", "--check", str(ROOT / "adapters/vscode/extension.js")],
        ["sh", "-n", str(ROOT / "adapters/codex/wrap.sh")],
        ["sh", "-n", str(ROOT / "daemon/hooks.sh")],
        [sys.executable, "-m", "py_compile", str(ROOT / "adapters/openclaw/adapter.py")],
        [sys.executable, "-m", "py_compile", str(ROOT / "daemon/daemon.py")],
        [sys.executable, "-m", "py_compile", str(ROOT / "daemon/agent_event.py")],
        [sys.executable, "-m", "py_compile", str(ROOT / "daemon/debugutil.py")],
    ]
    for cmd in checks:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            errors.append(f"syntax {' '.join(cmd)}: {r.stderr.strip()}")

    # sample validate + classify for each editor label
    samples = (
        ("vscode", "edit"), ("cursor", "focus"), ("neovim", "edit"),
        ("jetbrains", "edit"), ("godot", "play"), ("unity", "save"),
        ("claude-code", "tool"), ("codex", "tool"), ("openclaw", "tool"),
    )
    with tempfile.TemporaryDirectory(prefix="st-verify-") as td:
        log = Path(td) / "agent_events.jsonl"
        for editor, et in samples:
            payload = {
                "ts": 1, "event_type": et, "editor": editor,
                "agent": "human" if et in ("edit", "focus", "save", "play") else editor.split("-")[0],
                "files_touched_count": 0 if et in ("focus", "play") else 1,
                "op": "modified" if et in ("edit", "save") else None,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            ok, cleaned = agent_event.validate_event(payload)
            if not ok:
                errors.append(f"validate {editor}/{et}: {cleaned}")
                continue
            agent_event.emit(cleaned, path=log)
            routed = daemon.classify_exec(json.dumps({**cleaned, "path": "/must/not/leak.py"}))
            if routed.get("editor") != editor:
                errors.append(f"classify editor {editor} -> {routed.get('editor')}")
            if "path" in routed:
                errors.append(f"path leaked for {editor}")

    labels = {h["editor"] for h in editors.match_signatures(
        "nvim\ngodot\nunity\nkicad\nblender\nhx\nclion\ncodex\n"
    )}
    for need in ("neovim", "godot", "unity", "kicad", "blender", "helix", "jetbrains", "codex"):
        if need not in labels:
            errors.append(f"missing process label {need}")

    if not daemon.ignored_rel_path(".godot/x") or not daemon.ignored_rel_path("scene.blend1"):
        errors.append("ignore helpers broken")

    os.environ.pop("STRUGGLETIME_DEBUG", None)
    os.environ.pop("AITIME_DEBUG", None)
    if debugutil.debug_enabled():
        errors.append("debug should be off by default")
    os.environ["STRUGGLETIME_DEBUG"] = "1"
    if not debugutil.debug_enabled():
        errors.append("debug should honor STRUGGLETIME_DEBUG=1")
    os.environ.pop("STRUGGLETIME_DEBUG", None)

    for rel in ("server/static/index.html", "docs-site/lib/shared.ts", "docs-site/content/docs/setup.mdx"):
        text = (ROOT / rel).read_text()
        if old_repo in text:
            errors.append(f"{rel} still contains {old_repo}")
        if "doruk-aydin" in text or "origin.cursor.com/doruk" in text:
            errors.append(f"{rel} still points at the old host/user")

    # --debug flag present
    r = subprocess.run([sys.executable, str(ROOT / "daemon/daemon.py"), "--help"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or "--debug" not in r.stdout:
        errors.append("daemon.py missing --debug flag")


def run_e2e():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import tests.test_adapters_e2e as e2e
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(e2e)
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def main():
    errors = []
    static_checks(errors)
    if errors:
        print("verify_adapters: STATIC FAIL", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        return 1
    print("verify_adapters: static ok")
    print("--- end-to-end suite ---")
    result = run_e2e()
    if not result.wasSuccessful():
        print("verify_adapters: E2E FAIL", file=sys.stderr)
        return 1
    print("verify_adapters: ok (all adapters + hooks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
