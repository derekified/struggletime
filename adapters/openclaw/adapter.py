#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenClaw adapter: counts agent turns from local session logs.

OpenClaw's gateway executes tools itself, so a shell hook never sees
its prompts or exit codes. Its sessions are local JSONL though. This
adapter tails those files and emits ONLY counts (turns, tool calls,
failures) into the daemon's exec log. Message text, tool arguments,
and file contents are parsed past and never written anywhere.
"""
import argparse, json, os, time
from pathlib import Path

def classify(entry):
    s = json.dumps(entry).lower()
    base = {"editor": "openclaw", "agent": "openclaw"}
    if '"toolresult"' in s or '"tool_result"' in s or '"tool result"' in s:
        failed = '"iserror": true' in s or '"status": "error"' in s or '"exitcode": 1' in s
        return {**base, "category": "test_runner", "verb": "tool_result", "op": "modified", "exit_code": 1 if failed else 0}
    if '"toolcall"' in s or '"tool_call"' in s or '"tooluse"' in s:
        return {**base, "category": "ai_cli", "verb": "tool", "op": "modified", "exit_code": 0}
    role = str(entry.get("role", "")).lower() if isinstance(entry, dict) else ""
    typ = str(entry.get("type", "")).lower() if isinstance(entry, dict) else ""
    if role == "user" or typ in ("user_message", "human_turn"):
        return {**base, "category": "ai_cli", "verb": "prompt", "exit_code": 0}
    return None

def scan(sessions_dir, state):
    events = []
    for f in sorted(Path(sessions_dir).rglob("*.jsonl")):
        lines = f.read_text(errors="ignore").splitlines()
        start = state.get(str(f), 0)
        for line in lines[start:]:
            try: e = classify(json.loads(line))
            except Exception: continue
            if e: events.append(e)
        state[str(f)] = len(lines)
    return events

def _debug_enabled():
    v = (os.environ.get("STRUGGLETIME_DEBUG") or os.environ.get("AITIME_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-dir", default=os.environ.get("OPENCLAW_SESSIONS", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions")))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    # Unified bridge (agent_events.jsonl); fall back to legacy exec.jsonl only if forced
    exec_log = Path(
        os.environ.get("AITIME_AGENT_EVENTS")
        or os.environ.get("AITIME_EXEC_LOG")
        or (Path.home() / ".aitime" / "agent_events.jsonl")
    )
    exec_log.parent.mkdir(parents=True, exist_ok=True)
    state_file = exec_log.parent / "openclaw-adapter-state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    debug = _debug_enabled()
    if debug:
        print(json.dumps({"msg": "boot", "editor": "openclaw", "kind": "adapter"}), flush=True)
    while True:
        if Path(a.sessions_dir).exists():
            for e in scan(a.sessions_dir, state):
                e["ts"] = int(time.time())
                e["event_type"] = e.get("verb") or "tool"
                e["files_touched_count"] = 1 if e.get("op") else 0
                e["source"] = "adapter"
                # Never write message text / tool args — classify() already returns labels only
                with open(exec_log, "a") as fh: fh.write(json.dumps(e) + "\n")
                if debug:
                    print(json.dumps({
                        "msg": "emit", "editor": e.get("editor"), "event_type": e.get("event_type"),
                        "files_touched_count": e.get("files_touched_count"),
                    }), flush=True)
                else:
                    print(e, flush=True)
            state_file.write_text(json.dumps(state))
        if a.once: return
        time.sleep(2)

if __name__ == "__main__":
    main()
