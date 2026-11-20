#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lightweight agent/editor event bridge — no MCP, metadata only.

Adapters and the CLI append allowlisted JSON lines to
~/.aitime/agent_events.jsonl. daemon.py drains them on each poll tick.

  python3 daemon/agent_event.py --editor cursor --agent human --event-type edit --op modified
  python3 daemon/agent_event.py --editor claude-code --agent claude --event-type tool --exit-code 0
"""
from __future__ import annotations

import argparse, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE]
sys.path.insert(0, str(HERE))
import config as dcfg

# Compact allowlist — never paths, prompts, args, screenshots, keystrokes.
ALLOWED_EVENT_KEYS = {
    "ts", "event_type", "category", "verb", "exit_code",
    "editor", "agent", "op", "files_touched_count", "source", "project_id",
}
ALLOWED_EVENT_TYPES = {
    "edit", "focus", "blur", "tool", "prompt", "stop", "start", "exec", "created", "modified", "deleted",
}
ALLOWED_OPS = {"created", "modified", "deleted"}
FORBIDDEN = {
    "content", "text", "code", "prompt_text", "command", "args", "argv",
    "keystrokes", "screenshot", "image", "path", "filepath", "file_path",
    "raw", "transcript", "cwd", "workspace",
}

EVENT_LOG = dcfg.HOME / "agent_events.jsonl"
# Legacy path still drained by the daemon for older adapters.
EXEC_LOG = dcfg.HOME / "exec.jsonl"

EVENT_TO_CATEGORY = {
    "edit": "editor", "focus": "editor", "blur": "editor",
    "created": "editor", "modified": "editor", "deleted": "editor",
    "tool": "ai_cli", "prompt": "ai_cli", "stop": "ai_cli", "start": "ai_cli",
    "exec": "other",
}


def _clean_label(v, default=""):
    s = str(v or default).lower().strip().replace(" ", "-")
    if not s or len(s) > 32 or "/" in s or "\\" in s or " " in s:
        return default
    return s


def validate_event(raw):
    """Return (ok, cleaned_or_error). Strips unknown keys; rejects forbidden."""
    if not isinstance(raw, dict):
        return False, "event must be object"
    for k in raw:
        if k in FORBIDDEN:
            return False, f"forbidden key: {k}"
    evt = {k: raw[k] for k in raw if k in ALLOWED_EVENT_KEYS}
    event_type = _clean_label(evt.get("event_type") or evt.get("verb") or "", "")
    if event_type and event_type not in ALLOWED_EVENT_TYPES:
        event_type = "exec"
    cat = evt.get("category") or EVENT_TO_CATEGORY.get(event_type) or "other"
    if cat not in ("git", "test_runner", "compiler", "ai_cli", "other", "editor", "formatter"):
        cat = "other"
    op = evt.get("op") if evt.get("op") in ALLOWED_OPS else None
    if not op and event_type in ALLOWED_OPS:
        op = event_type
    try:
        exit_code = int(evt.get("exit_code") or 0)
    except (TypeError, ValueError):
        exit_code = 0
    try:
        files_n = int(evt.get("files_touched_count") or 0)
    except (TypeError, ValueError):
        files_n = 0
    files_n = max(0, min(files_n, 500))
    try:
        ts = int(evt.get("ts") or time.time())
    except (TypeError, ValueError):
        ts = int(time.time())
    out = {
        "ts": ts,
        "event_type": event_type or "exec",
        "category": cat,
        "verb": _clean_label(evt.get("verb"), event_type or "exec")[:32],
        "exit_code": exit_code,
        "editor": _clean_label(evt.get("editor"), "") or None,
        "agent": _clean_label(evt.get("agent"), "") or None,
        "op": op,
        "files_touched_count": files_n,
        "source": "adapter",
    }
    pid = _clean_label(evt.get("project_id"), "")
    if pid:
        out["project_id"] = pid
    return True, out


def emit(event, path=None):
    """Validate and append one event. Returns cleaned dict or raises ValueError."""
    ok, cleaned = validate_event(event if isinstance(event, dict) else {})
    if not ok:
        raise ValueError(cleaned)
    dest = Path(path or os.environ.get("AITIME_AGENT_EVENTS") or EVENT_LOG)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "a") as fh:
        fh.write(json.dumps(cleaned, separators=(",", ":")) + "\n")
    return cleaned


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aitime-agent-event", description="Emit one allowlisted IDE/agent event")
    ap.add_argument("--editor", default="")
    ap.add_argument("--agent", default="")
    ap.add_argument("--event-type", default="edit", help="edit|focus|tool|prompt|stop|exec|…")
    ap.add_argument("--category", default="")
    ap.add_argument("--verb", default="")
    ap.add_argument("--op", default="", choices=["", "created", "modified", "deleted"])
    ap.add_argument("--exit-code", type=int, default=0)
    ap.add_argument("--files-touched-count", type=int, default=0)
    ap.add_argument("--json", default="", help="raw JSON object (still allowlisted)")
    a = ap.parse_args(argv)
    if a.json:
        try:
            payload = json.loads(a.json)
        except json.JSONDecodeError as e:
            print(f"bad json: {e}", file=sys.stderr)
            return 1
    else:
        payload = {
            "ts": int(time.time()),
            "event_type": a.event_type,
            "category": a.category or None,
            "verb": a.verb or a.event_type,
            "editor": a.editor or None,
            "agent": a.agent or None,
            "op": a.op or None,
            "exit_code": a.exit_code,
            "files_touched_count": a.files_touched_count,
        }
    try:
        cleaned = emit(payload)
    except ValueError as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    print(json.dumps(cleaned), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
