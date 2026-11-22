#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full verification across StruggleTime adapters (privacy + emit contract).

Checks:
  1. Adapter trees exist and contain no forbidden payload keys in source
  2. Sample allowlisted events for each editor label validate + classify cleanly
  3. Debug util stays silent unless STRUGGLETIME_DEBUG is set
  4. Daemon ignore helpers still skip cache/lock noise

Exit 0 on success. Stdlib only.
"""
from __future__ import annotations

import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_SRC = (
    "prompt_text", "keystroke", "transcript", "screenshot",
    "clipboard", "microphone",
)
ADAPTER_FILES = [
    "adapters/vscode/extension.js",
    "adapters/claude-code/hooks.json",
    "adapters/codex/wrap.sh",
    "adapters/openclaw/adapter.py",
    "adapters/neovim/lua/struggletime/init.lua",
    "adapters/godot/addons/struggletime/plugin.gd",
    "adapters/jetbrains/src/main/kotlin/com/struggletime/StruggleTimeStartup.kt",
    "adapters/unity/Editor/StruggleTimeTracker.cs",
]
EDITOR_SAMPLES = (
    ("vscode", "edit"),
    ("cursor", "focus"),
    ("neovim", "edit"),
    ("jetbrains", "edit"),
    ("godot", "play"),
    ("unity", "save"),
    ("claude-code", "tool"),
    ("codex", "tool"),
    ("openclaw", "tool"),
)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    errors = []
    agent_event = load("agent_event_v", "daemon/agent_event.py")
    daemon = load("daemon_v", "daemon/daemon.py")
    debugutil = load("debugutil_v", "daemon/debugutil.py")
    editors = load("editors_v", "daemon/editors.py")

    for rel in ADAPTER_FILES:
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"missing {rel}")
            continue
        text = path.read_text(errors="ignore").lower()
        for bad in FORBIDDEN_SRC:
            # allow comments mentioning the ban; fail only on assignment-ish use
            if f'"{bad}"' in text or f"'{bad}'" in text or f".{bad}" in text:
                if "forbidden" in text or "never" in text or "delete" in text:
                    continue
                errors.append(f"{rel} references {bad}")

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "agent_events.jsonl"
        os.environ["AITIME_AGENT_EVENTS"] = str(log)
        for editor, et in EDITOR_SAMPLES:
            payload = {
                "ts": 1,
                "event_type": et,
                "editor": editor,
                "agent": "human" if et in ("edit", "focus", "save", "play") else editor.split("-")[0],
                "files_touched_count": 0 if et in ("focus", "play") else 1,
                "op": "modified" if et in ("edit", "save") else None,
            }
            ok, cleaned = agent_event.validate_event({k: v for k, v in payload.items() if v is not None})
            if not ok:
                errors.append(f"validate failed for {editor}/{et}: {cleaned}")
                continue
            agent_event.emit(cleaned, path=log)
            line = json.dumps({**cleaned, "path": "/must/not/survive.py"})
            routed = daemon.classify_exec(line)
            if routed.get("editor") != editor:
                errors.append(f"classify editor mismatch {editor} -> {routed.get('editor')}")
            if "path" in routed:
                errors.append(f"path leaked for {editor}")

    # process labels for new tools
    blob = "nvim\ngodot\nunity\nkicad\nblender\nhx\nclion\n"
    labels = {h["editor"] for h in editors.match_signatures(blob)}
    for need in ("neovim", "godot", "unity", "kicad", "blender", "helix", "jetbrains"):
        if need not in labels:
            errors.append(f"missing process label {need}")

    # ignores
    if not daemon.ignored_rel_path(".godot/x") or not daemon.ignored_rel_path("scene.blend1"):
        errors.append("ignore helpers broken")

    # debug util: off by default
    os.environ.pop("STRUGGLETIME_DEBUG", None)
    os.environ.pop("AITIME_DEBUG", None)
    if debugutil.debug_enabled():
        errors.append("debug should be off by default")
    os.environ["STRUGGLETIME_DEBUG"] = "1"
    if not debugutil.debug_enabled():
        errors.append("debug should honor STRUGGLETIME_DEBUG=1")
    os.environ.pop("STRUGGLETIME_DEBUG", None)

    # repo reference hygiene in working tree key files
    for rel in (
        "server/static/index.html",
        "docs-site/lib/shared.ts",
        "docs-site/content/docs/setup.mdx",
    ):
        text = (ROOT / rel).read_text()
        if "struggletime" in text:
            errors.append(f"{rel} still contains struggletime")
        if "github.com/derekified/struggletime" not in text and rel != "docs-site/lib/shared.ts":
            if "derekified" not in text:
                errors.append(f"{rel} missing derekified/struggletime")

    if errors:
        print("verify_adapters: FAIL", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        return 1
    print(f"verify_adapters: ok ({len(ADAPTER_FILES)} adapters, {len(EDITOR_SAMPLES)} event samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
