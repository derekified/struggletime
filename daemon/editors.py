#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extensible editor / AI-agent signature registry for Layer-1 attribution.

Built-in signatures cover major + niche IDEs/CLIs. Users can override or
extend via ~/.config/struggletime/editors.json without per-IDE plugins. Stdlib only.
"""
from __future__ import annotations

import json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE]
sys.path.insert(0, str(HERE))
import config as dcfg

# kind: ai_ide | editor | cli_agent
# match: substring against lowercased process blob (comm + short args)
BUILTIN_SIGNATURES = [
    # AI-native IDEs
    {"match": "cursor", "editor": "cursor", "kind": "ai_ide"},
    {"match": "windsurf", "editor": "windsurf", "kind": "ai_ide"},
    {"match": "trae", "editor": "trae", "kind": "ai_ide"},
    {"match": "kiro", "editor": "kiro", "kind": "ai_ide"},
    {"match": "zed", "editor": "zed", "kind": "ai_ide"},
    {"match": "antigravity", "editor": "antigravity", "kind": "ai_ide"},
    # Standard editors / forks
    {"match": "vscodium", "editor": "vscodium", "kind": "editor"},
    {"match": "code - oss", "editor": "vscodium", "kind": "editor"},
    {"match": "code helper", "editor": "vscode", "kind": "editor"},
    {"match": "visual studio code", "editor": "vscode", "kind": "editor"},
    {"match": "code.exe", "editor": "vscode", "kind": "editor"},
    {"match": "codium", "editor": "vscodium", "kind": "editor"},
    {"match": "nvim", "editor": "neovim", "kind": "editor"},
    {"match": "helix", "editor": "helix", "kind": "editor"},
    {"match": "hx", "editor": "helix", "kind": "editor"},
    {"match": "kakoune", "editor": "kakoune", "kind": "editor"},
    {"match": "sublime_text", "editor": "sublime", "kind": "editor"},
    {"match": "subl", "editor": "sublime", "kind": "editor"},
    {"match": "emacs", "editor": "emacs", "kind": "editor"},
    {"match": "vim", "editor": "vim", "kind": "editor"},
    # JetBrains family
    {"match": "idea", "editor": "jetbrains", "kind": "editor"},
    {"match": "webstorm", "editor": "jetbrains", "kind": "editor"},
    {"match": "pycharm", "editor": "jetbrains", "kind": "editor"},
    {"match": "goland", "editor": "jetbrains", "kind": "editor"},
    {"match": "clion", "editor": "jetbrains", "kind": "editor"},
    {"match": "rider", "editor": "jetbrains", "kind": "editor"},
    {"match": "rustrover", "editor": "jetbrains", "kind": "editor"},
    {"match": "android studio", "editor": "jetbrains", "kind": "editor"},
    {"match": "studio64", "editor": "jetbrains", "kind": "editor"},
    {"match": "fleet", "editor": "fleet", "kind": "editor"},
    {"match": "nova", "editor": "nova", "kind": "editor"},
    {"match": "textmate", "editor": "textmate", "kind": "editor"},
    {"match": "kate", "editor": "kate", "kind": "editor"},
    {"match": "gedit", "editor": "gedit", "kind": "editor"},
    {"match": "notepad++", "editor": "notepadpp", "kind": "editor"},
    {"match": "xcode", "editor": "xcode", "kind": "editor"},
    {"match": "arduino-ide", "editor": "arduino", "kind": "editor"},
    {"match": "arduino ide", "editor": "arduino", "kind": "editor"},
    # Game / creative / CAD tools (process fallback when files change)
    {"match": "godot", "editor": "godot", "kind": "editor"},
    {"match": "unity", "editor": "unity", "kind": "editor"},
    {"match": "unity hub", "editor": "unity", "kind": "editor"},
    {"match": "kicad", "editor": "kicad", "kind": "editor"},
    {"match": "pcbnew", "editor": "kicad", "kind": "editor"},
    {"match": "eeschema", "editor": "kicad", "kind": "editor"},
    {"match": "gerbview", "editor": "kicad", "kind": "editor"},
    {"match": "blender", "editor": "blender", "kind": "editor"},
    {"match": "freecad", "editor": "freecad", "kind": "editor"},
    {"match": "openscad", "editor": "openscad", "kind": "editor"},
    {"match": "aseprite", "editor": "aseprite", "kind": "editor"},
    {"match": "krita", "editor": "krita", "kind": "editor"},
    {"match": "gimp", "editor": "gimp", "kind": "editor"},
    {"match": "figma", "editor": "figma", "kind": "editor"},
    {"match": "jupyter-lab", "editor": "jupyter", "kind": "editor"},
    {"match": "jupyter-notebook", "editor": "jupyter", "kind": "editor"},
    {"match": "jupyter", "editor": "jupyter", "kind": "editor"},
    {"match": "rstudio", "editor": "rstudio", "kind": "editor"},
    {"match": "obsidian", "editor": "obsidian", "kind": "editor"},
    # CLI / desktop AI agents (agent id may differ from editor label)
    {"match": "claude-code", "editor": "claude-code", "agent": "claude", "kind": "cli_agent"},
    {"match": "claude", "editor": "claude-code", "agent": "claude", "kind": "cli_agent"},
    {"match": "codex", "editor": "codex", "agent": "codex", "kind": "cli_agent"},
    {"match": "chatgpt", "editor": "codex", "agent": "codex", "kind": "cli_agent"},
    {"match": "openai", "editor": "codex", "agent": "codex", "kind": "cli_agent"},
    {"match": "openclaw", "editor": "openclaw", "agent": "openclaw", "kind": "cli_agent"},
    {"match": "aider", "editor": "aider", "agent": "aider", "kind": "cli_agent"},
    {"match": "goose", "editor": "goose", "agent": "goose", "kind": "cli_agent"},
    {"match": "opencode", "editor": "opencode", "agent": "opencode", "kind": "cli_agent"},
    {"match": "gemini-cli", "editor": "gemini-cli", "agent": "gemini", "kind": "cli_agent"},
    {"match": "gemini", "editor": "gemini-cli", "agent": "gemini", "kind": "cli_agent"},
    {"match": "cursor-agent", "editor": "cursor", "agent": "cursor-agent", "kind": "cli_agent"},
    {"match": "amp", "editor": "amp", "agent": "amp", "kind": "cli_agent"},
    {"match": "crush", "editor": "crush", "agent": "crush", "kind": "cli_agent"},
    {"match": "qwen", "editor": "qwen", "agent": "qwen", "kind": "cli_agent"},
    {"match": "cline", "editor": "cline", "agent": "cline", "kind": "cli_agent"},
    {"match": "roo-code", "editor": "roo", "agent": "roo", "kind": "cli_agent"},
    {"match": "roo", "editor": "roo", "agent": "roo", "kind": "cli_agent"},
    {"match": "continue", "editor": "continue", "agent": "continue", "kind": "cli_agent"},
    {"match": "copilot", "editor": "copilot", "agent": "copilot", "kind": "cli_agent"},
]

# Process basenames that look like terminals — used only as last-resort context, never as editor id alone
_TERMINAL_NOISE = {
    "bash", "zsh", "sh", "fish", "nu", "pwsh", "powershell", "cmd", "conhost",
    "python", "python3", "node", "ruby", "perl", "java", "sshd", "systemd",
    "tmux", "screen", "login", "init", "caffeinate",
}

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,31}$")
_CACHE = {"mtime": None, "sigs": None, "blob": None, "blob_at": 0.0}


def _token_hit(needle, blob):
    """Substring match with alnum boundaries so 'vim' ≠ 'nvim'."""
    if not needle or needle not in blob:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", blob) is not None

EDITORS_JSON = dcfg.EDITORS_JSON


def _clean_label(v, default=""):
    s = str(v or default).lower().strip().replace(" ", "-")
    if not s or not _LABEL_RE.match(s) or "/" in s or "\\" in s:
        return default
    if s in ("local-daemon", "local_daemon", "untracked"):
        return default
    return s


def _load_user_signatures():
    path = dcfg.editors_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return list(BUILTIN_SIGNATURES), None
    if _CACHE["mtime"] == mtime and _CACHE["sigs"] is not None:
        return _CACHE["sigs"], mtime
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return list(BUILTIN_SIGNATURES), mtime
    sigs = list(BUILTIN_SIGNATURES)
    # User entries win: prepend so they match first
    extra = []
    for row in (raw.get("signatures") or raw.get("editors") or []):
        if not isinstance(row, dict):
            continue
        match = str(row.get("match") or row.get("process") or "").lower().strip()
        editor = _clean_label(row.get("editor") or row.get("id") or match, "")
        if not match or not editor:
            continue
        kind = row.get("kind") or "editor"
        if kind not in ("ai_ide", "editor", "cli_agent"):
            kind = "editor"
        agent = _clean_label(row.get("agent") or "", "") or None
        if kind == "cli_agent" and not agent:
            agent = editor
        extra.append({"match": match, "editor": editor, "kind": kind, "agent": agent})
    # aliases: {"code": "vscode"} → match "code" as editor vscode
    for src, dst in (raw.get("aliases") or {}).items():
        src_l = str(src).lower().strip()
        dst_l = _clean_label(dst, "")
        if src_l and dst_l:
            extra.append({"match": src_l, "editor": dst_l, "kind": "editor"})
    merged = extra + sigs
    _CACHE["mtime"] = mtime
    _CACHE["sigs"] = merged
    return merged, mtime


def process_blob():
    """Lowercased process snapshot (comm + short args). Cached ~2s."""
    import time
    now = time.monotonic()
    if _CACHE["blob"] is not None and now - _CACHE["blob_at"] < 2.0:
        return _CACHE["blob"]
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=3).stdout
        else:
            # comm + args (truncated) so "Windsurf Helper" / Electron apps match
            out = subprocess.run(
                ["ps", "-ax", "-o", "comm=,args="],
                capture_output=True, text=True, timeout=3,
            ).stdout
    except Exception:
        out = ""
    blob = (out or "").lower()
    _CACHE["blob"] = blob
    _CACHE["blob_at"] = now
    return blob


def match_signatures(blob=None):
    """Return list of {editor, agent, kind} hits, highest priority first."""
    blob = blob if blob is not None else process_blob()
    sigs, _ = _load_user_signatures()
    hits = []
    seen = set()
    for sig in sigs:
        needle = sig["match"]
        if not _token_hit(needle, blob):
            continue
        editor = sig["editor"]
        kind = sig.get("kind") or "editor"
        agent = sig.get("agent")
        if kind == "cli_agent" and not agent:
            agent = editor
        key = (editor, agent or "", kind)
        if key in seen:
            continue
        seen.add(key)
        hits.append({"editor": editor, "agent": agent, "kind": kind})
    return hits


def detect_running_editors(blob=None):
    hits = match_signatures(blob)
    out = []
    for h in hits:
        if h["kind"] in ("ai_ide", "editor") and h["editor"] not in out:
            out.append(h["editor"])
    env_ed = _clean_label(
        os.environ.get("STRUGGLETIME_EDITOR") or os.environ.get("AITIME_EDITOR") or "", ""
    )
    if env_ed and env_ed not in out:
        out.insert(0, env_ed)
    return out


def detect_running_agents(blob=None):
    hits = match_signatures(blob)
    out = []
    for h in hits:
        if h["kind"] == "cli_agent" and h.get("agent") and h["agent"] not in out:
            out.append(h["agent"])
        elif h["kind"] == "ai_ide" and h["editor"] not in out:
            # AI IDEs imply an in-editor agent context when burst looks automated
            pass
    env_ag = _clean_label(
        os.environ.get("STRUGGLETIME_AGENT") or os.environ.get("AITIME_AGENT") or "", ""
    )
    if env_ag and env_ag not in out:
        out.insert(0, env_ag)
    return out


def infer_unknown_editor(blob=None):
    """Sanitize a foreground-ish process name when no registry hit exists."""
    blob = blob if blob is not None else process_blob()
    # Prefer lines that look like GUI/Electron apps over shells
    candidates = []
    for line in blob.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        base = Path(parts[0]).name.lower()
        base = re.sub(r"\.exe$|\.app$", "", base)
        base = base.strip("-_")
        if not base or base in _TERMINAL_NOISE:
            continue
        if any(x in base for x in ("helper", "renderer", "gpu", "crashpad", "plugin")):
            continue
        base = re.sub(r"[^a-z0-9._+-]", "", base)[:32]
        label = _clean_label(base, "")
        if label and len(label) >= 2:
            candidates.append(label)
    # unique preserve order
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:5]


def classify_active(burst_kind="human", hint=None):
    """Pick (editor, agent, kind, source) for this poll window.

    Never returns local-daemon / untracked when a known or inferred process is up.
    Honors STRUGGLETIME_EDITOR / AITIME_EDITOR and STRUGGLETIME_AGENT / AITIME_AGENT
    for passive tests and headless attribution without a live IDE process.
    """
    hint = hint or {}
    blob = process_blob()
    hits = match_signatures(blob)
    editors = [h["editor"] for h in hits if h["kind"] in ("ai_ide", "editor")]
    agents = [h["agent"] for h in hits if h["kind"] == "cli_agent" and h.get("agent")]
    ai_ides = [h["editor"] for h in hits if h["kind"] == "ai_ide"]
    env_ed = _clean_label(
        os.environ.get("STRUGGLETIME_EDITOR") or os.environ.get("AITIME_EDITOR") or "", ""
    )
    env_ag = _clean_label(
        os.environ.get("STRUGGLETIME_AGENT") or os.environ.get("AITIME_AGENT") or "", ""
    )
    if env_ed and env_ed not in editors:
        editors.insert(0, env_ed)
    if env_ag and env_ag not in agents:
        agents.insert(0, env_ag)

    editor = _clean_label(hint.get("editor"), "") or (editors[0] if editors else "")
    if not editor:
        inferred = infer_unknown_editor(blob)
        editor = inferred[0] if inferred else "unknown"

    if hint.get("agent"):
        agent = _clean_label(hint["agent"], "human")
        kind = "cli_agent"
    elif burst_kind == "ai":
        if agents:
            agent = agents[0]
            kind = "cli_agent"
        elif ai_ides:
            agent = ai_ides[0] + "-agent"
            kind = "ai_ide"
            if not hint.get("editor"):
                editor = ai_ides[0]
        else:
            agent = "unknown-agent"
            kind = "cli_agent"
    else:
        agent = "human"
        kind = "ai_ide" if editor in ai_ides else ("editor" if editor in editors else "editor")

    source = hint.get("source") or ("adapter" if hint.get("editor") or hint.get("agent") else "layer1")
    return editor or "unknown", agent or "human", kind, source


def write_example_config(path=None):
    """Optional helper — does not run automatically."""
    p = Path(path or EDITORS_JSON)
    example = {
        "signatures": [
            {"match": "my-custom-ide", "editor": "my-custom-ide", "kind": "ai_ide"},
            {"match": "my-agent-cli", "editor": "my-agent-cli", "agent": "my-agent", "kind": "cli_agent"},
        ],
        "aliases": {},
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(json.dumps(example, indent=2) + "\n")
    return p
