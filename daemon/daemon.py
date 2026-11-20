#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layer-1 aitime daemon: polling watcher + agent event bridge -> chained buckets.

Stdlib only, cross-platform (no native file watchers, no MCP). Metadata only —
raw paths never leave this process (anonymized file ids in the ledger salt).

Drains ~/.aitime/agent_events.jsonl (adapters + agent-event CLI) and legacy
exec.jsonl on each poll tick; falls back to process/workspace inference when
no adapter fired in the window.
"""
import argparse, json, os, platform, signal, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
from verify import bucket_hash, anonymized_file_id, GENESIS
import config as dcfg
import agent_event as agent_evt
import editors as edreg

IGNORE = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".aitime"}
_STOP = False

# Shell hook categories (name only — never arguments)
EXEC_CATS = {
    "git": "git",
    "pytest": "test_runner", "jest": "test_runner", "vitest": "test_runner", "test": "test_runner",
    "go": "test_runner", "cargo": "test_runner", "npm": "test_runner", "pnpm": "test_runner",
    "yarn": "test_runner", "make": "test_runner", "python": "test_runner", "python3": "test_runner", "node": "test_runner",
    "gcc": "compiler", "g++": "compiler", "clang": "compiler", "clang++": "compiler",
    "rustc": "compiler", "javac": "compiler", "tsc": "compiler",
    "claude": "ai_cli", "codex": "ai_cli", "aider": "ai_cli", "opencode": "ai_cli", "openclaw": "ai_cli",
    "gemini": "ai_cli", "goose": "ai_cli", "amp": "ai_cli", "crush": "ai_cli", "copilot": "ai_cli",
    "cursor-agent": "ai_cli", "qwen": "ai_cli", "cline": "ai_cli", "roo": "ai_cli",
}

# Extension → language (never send paths; only this label)
LANG_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".java": "java",
    ".kt": "kotlin", ".swift": "swift", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".fs": "fsharp", ".scala": "scala", ".lua": "lua",
    ".r": "r", ".jl": "julia", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css",
    ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".xml": "xml", ".vue": "vue", ".svelte": "svelte", ".dart": "dart", ".ex": "elixir",
    ".exs": "elixir", ".hs": "haskell", ".ml": "ocaml", ".zig": "zig", ".nim": "nim",
}

def env(*keys, default=""):
    return dcfg.env(*keys, default=default)

def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def git_anchor(repo, salt=None, bursts=None, file_meta=None):
    """Build git totals + anonymized per-file rows (optional op/editor/agent)."""
    added = deleted = 0
    files = {}
    file_meta = file_meta or {}
    for line in git(repo, "diff", "--numstat").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            a = int(p[0]) if p[0].isdigit() else 0
            d = int(p[1]) if p[1].isdigit() else 0
            added += a
            deleted += d
            if salt and len(p) >= 3:
                rel = p[2]
                fid = anonymized_file_id(rel, salt)
                row = {"file_id": fid, "added": a, "deleted": d, "bursts": (bursts or {}).get(rel, 0)}
                row.update({k: v for k, v in (file_meta.get(rel) or {}).items() if k in ("op", "editor", "agent", "source", "language")})
                files[fid] = row
    for rel, n in (bursts or {}).items():
        fid = anonymized_file_id(rel, salt) if salt else None
        if salt and fid not in files:
            row = {"file_id": fid, "added": 0, "deleted": 0, "bursts": n}
            row.update({k: v for k, v in (file_meta.get(rel) or {}).items() if k in ("op", "editor", "agent", "source", "language")})
            files[fid] = row
    # deleted files may only appear in file_meta
    for rel, meta in file_meta.items():
        if meta.get("op") != "deleted" or not salt:
            continue
        fid = anonymized_file_id(rel, salt)
        if fid not in files:
            row = {"file_id": fid, "added": 0, "deleted": 0, "bursts": (bursts or {}).get(rel, 0)}
            row.update({k: v for k, v in meta.items() if k in ("op", "editor", "agent", "source", "language")})
            files[fid] = row
    dirty = len([l for l in git(repo, "status", "--porcelain").splitlines() if l])
    anchor = {
        "head_commit": git(repo, "rev-parse", "HEAD") or None,
        "lines_added": added, "lines_deleted": deleted, "dirty_files": dirty,
    }
    return anchor, list(files.values())[:50]

def snapshot(repo):
    out = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in IGNORE]
        for f in files:
            p = Path(root) / f
            try:
                out[str(p.relative_to(repo))] = p.stat().st_mtime_ns
            except Exception:
                pass
    return out

def classify_name(name):
    name = (name or "").lower().strip()
    if name in EXEC_CATS:
        return EXEC_CATS[name]
    return "other"

def _clean_label(v, default="unknown"):
    s = str(v or default).lower().strip().replace(" ", "-")
    if not s or len(s) > 32 or "/" in s or "\\" in s or " " in s:
        return default
    return s

def classify_exec(line):
    """Parse shell-hook / adapter / agent_events JSONL. Never keep args or paths."""
    e = json.loads(line)
    if not isinstance(e, dict):
        raise ValueError("exec line must be object")
    # Prefer unified agent_event allowlist when keys look like bridge events
    if any(k in e for k in ("event_type", "editor", "agent", "files_touched_count")):
        ok, cleaned = agent_evt.validate_event(e)
        if ok:
            return cleaned
    cat = e.get("category") or classify_name(e.get("name") or e.get("cmd"))
    if cat not in ("git", "test_runner", "compiler", "ai_cli", "other", "editor"):
        cat = "other"
    verb = e.get("verb") or e.get("event_type") or ""
    if " " in verb or "/" in verb or len(verb) > 32:
        verb = ""
    return {
        "category": cat,
        "verb": verb,
        "event_type": _clean_label(e.get("event_type") or verb, "exec"),
        "exit_code": int(e.get("exit_code") or 0),
        "ts": e.get("ts"),
        "editor": _clean_label(e.get("editor"), "") or None,
        "agent": _clean_label(e.get("agent"), "") or None,
        "op": e.get("op") if e.get("op") in ("created", "modified", "deleted") else None,
        "files_touched_count": int(e.get("files_touched_count") or 0),
        "source": "adapter" if e.get("editor") or e.get("agent") or cat == "editor" else "layer1",
    }

def drain_event_log(path, offset_key, state, counts, exec_categories, attr_hint):
    """Read new JSONL lines from an event bridge file; mutate counts/hint. Returns updated hint."""
    path = Path(path)
    if not path.exists():
        return attr_hint
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return attr_hint
    start = int(state.get(offset_key) or 0)
    for line in lines[start:]:
        try:
            e = classify_exec(line)
        except Exception:
            continue
        counts["executions"] += 1
        cat = e.get("category") or "other"
        exec_categories[cat] = exec_categories.get(cat, 0) + 1
        if e.get("exit_code"):
            counts["exec_failures"] += 1
        if cat == "ai_cli":
            counts["prompts"] += 1
            attr_hint = {
                "agent": e.get("agent") or _clean_label(e.get("verb") or "ai-cli", "unknown-agent"),
                "editor": e.get("editor") or attr_hint.get("editor"),
                "source": "adapter",
            }
        if cat == "editor":
            eds = edreg.detect_running_editors()
            attr_hint = {
                "editor": e.get("editor") or (eds[0] if eds else "unknown"),
                "agent": e.get("agent") or "human",
                "source": "adapter",
            }
        if cat == "git" and e.get("verb") == "commit":
            counts["commits"] += 1
        # files_touched_count from adapters can reinforce burst attribution without paths
        n = int(e.get("files_touched_count") or 0)
        if n >= 5 and cat in ("editor", "ai_cli"):
            counts["ai_writes"] += max(1, n // 5)
            if not attr_hint.get("agent") or attr_hint.get("agent") == "human":
                attr_hint = {
                    **attr_hint,
                    "agent": e.get("agent") or (edreg.detect_running_agents() or ["unknown-agent"])[0],
                    "source": attr_hint.get("source") or "adapter",
                }
    state[offset_key] = len(lines)
    return attr_hint

def empty():
    return {
        "human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0,
        "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
    }

def classify_burst(n_changed):
    """1–2 files = human; 5+ files = agent/formatter."""
    return "ai" if n_changed >= 5 else "human"

def detect_running_editors():
    return edreg.detect_running_editors()

def detect_running_agents():
    return edreg.detect_running_agents()

def host_os():
    """Populate os via stdlib platform — never 'untracked'."""
    sysname = (platform.system() or "").lower()
    if sysname == "darwin":
        label = "macos"
    elif sysname.startswith("win"):
        label = "windows"
    else:
        label = "linux"
    return {
        "id": label,
        "release": _clean_label(platform.release(), "unknown")[:32] or "unknown",
        "arch": _clean_label(platform.machine(), "unknown")[:16] or "unknown",
    }

def language_from_rel(rel):
    """Infer language from file extension only (path never stored)."""
    ext = Path(rel or "").suffix.lower()
    return LANG_EXT.get(ext) or "other"

def default_attr(burst_kind="human", hint=None):
    """Extensible registry + universal fallback — never local-daemon when a tool is running."""
    editor, agent, _kind, source = edreg.classify_active(burst_kind, hint)
    return editor, agent, source

def diff_ops(prev_snap, snap):
    """Return {rel: op} for created / modified / deleted in this poll."""
    ops = {}
    for rel in snap:
        if rel not in prev_snap:
            ops[rel] = "created"
        elif snap[rel] != prev_snap[rel]:
            ops[rel] = "modified"
    for rel in prev_snap:
        if rel not in snap:
            ops[rel] = "deleted"
    return ops

def post_buckets(server, user, token, buckets, retries=4):
    url = server.rstrip("/") + "/v1/buckets/batch"
    body = json.dumps({"user_id": user, "buckets": buckets}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method="POST", data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return True, resp.read().decode()
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            if e.code in (400, 401, 403, 409, 422):
                return False, f"http {e.code}: {raw}"
            last_err = f"http {e.code}: {raw}"
        except Exception as e:
            last_err = str(e)
        time.sleep(min(2 ** attempt, 8))
    return False, last_err or "upload failed"

def fetch_chain_head(server, token, project):
    """Return server last_hash for project, or None if unreachable/unauthorized."""
    if not server or not project:
        return None
    url = server.rstrip("/") + "/v1/projects/" + urllib.request.quote(project, safe="") + "/chain"
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("last_hash") or GENESIS
    except Exception:
        return None

def adopt_chain_head(state, server, token, project, ledger=None):
    """Align local prev_hash with the server so a fresh ledger can continue a project."""
    head = fetch_chain_head(server, token, project)
    if head is None:
        return False
    if state.get("prev_hash") == head:
        return False
    # Only auto-adopt when we have nothing pending, or pending still starts at GENESIS
    pending = state.get("pending") or []
    if pending and pending[0].get("prev_hash") not in (GENESIS, head):
        return False
    dropped = len(pending)
    state["pending"] = []
    state["prev_hash"] = head
    if ledger is not None:
        save_ledger(ledger, state)
    print(
        f"adopted server chain head for {project}"
        + (f" (dropped {dropped} unsynced bucket(s))" if dropped else ""),
        flush=True,
    )
    return True

def save_ledger(ledger, state):
    ledger.write_text(json.dumps(state, indent=2))

def flush_pending(state, server, user, token, ledger, project=None):
    if not server or not state.get("pending"):
        return False
    ok, msg = post_buckets(server, user, token, state["pending"])
    if ok:
        print(f"synced {len(state['pending'])} bucket(s): {msg}", flush=True)
        state["pending"] = []
        save_ledger(ledger, state)
        return True
    print(f"sync deferred ({len(state['pending'])} pending): {msg}", flush=True)
    # Fresh ledger vs existing server chain — adopt head so the next seal can sync
    if "prev_hash mismatch" in (msg or "") and project:
        if adopt_chain_head(state, server, token, project, ledger):
            return False
    return False

def _on_signal(signum, _frame):
    global _STOP
    if signum in (signal.SIGTERM, signal.SIGINT):
        print(f"signal {signum}: shutting down…", flush=True)
        _STOP = True
    elif hasattr(signal, "SIGUSR2") and signum == signal.SIGUSR2:
        os.environ["_ST_FLUSH_NOW"] = "1"

def seal_bucket(state, repo, project, bucket_secs, started_wall, started_mono, counts, bursts, install_id, file_meta=None, exec_categories=None, rel_langs=None, attr_hint=None):
    file_meta = file_meta or {}
    rel_langs = rel_langs or {}
    attr_hint = attr_hint or {}
    anchor, files = git_anchor(repo, state["salt"], bursts, file_meta=file_meta)
    if (anchor["lines_added"] + anchor["lines_deleted"]) >= 200 and counts["human_edits"] > 0 and counts["ai_writes"] == 0:
        counts = dict(counts)
        counts["ai_writes"] += counts["human_edits"]
        counts["human_edits"] = 0
        for meta in file_meta.values():
            if meta.get("agent") == "human":
                meta["agent"] = (detect_running_agents() or ["unknown-agent"])[0]
    # Attach language labels onto anonymized file rows (extension-derived only)
    fid_lang = {}
    for rel, lang in rel_langs.items():
        fid_lang[anonymized_file_id(rel, state["salt"])] = lang
    for f in files:
        lang = fid_lang.get(f.get("file_id"))
        if lang:
            f["language"] = lang
    editors, agents, languages = {}, {}, {}
    for f in files:
        ed, ag = f.get("editor") or "unknown", f.get("agent") or "unknown"
        if ed in ("local-daemon", "local_daemon", ""):
            ed = (detect_running_editors() or ["unknown"])[0]
            f["editor"] = ed
        editors[ed] = editors.get(ed, 0) + 1
        agents[ag] = agents.get(ag, 0) + 1
        lang = f.get("language") or "other"
        languages[lang] = languages.get(lang, 0) + 1
    if not editors:
        # Prefer adapter hint, then running IDE — never invent local-daemon
        ed, ag, _ = default_attr("human", attr_hint)
        editors[ed] = 1
        if ag and ag not in agents:
            agents[ag] = agents.get(ag, 0) + 1
    if not languages:
        languages["other"] = 1
    os_info = host_os()
    bucket = {
        "v": 1, "bucket_id": str(uuid.uuid4()), "install_id": install_id,
        "project_id": project, "repo_remote_hash": None, "bucket_start_wall": started_wall,
        "bucket_secs": bucket_secs, "mono_delta_ms": int((time.monotonic() - started_mono) * 1000),
        "counts": counts, "git": anchor, "files": files,
        "exec_categories": exec_categories or {},
        "editors": editors, "agents": agents, "languages": languages,
        "os": os_info["id"], "os_detail": os_info,
        "formula_version": "v0.1.0-alpha", "prev_hash": state["prev_hash"],
    }
    bucket["hash"] = bucket_hash(bucket)
    state["prev_hash"] = bucket["hash"]
    state["pending"].append(bucket)
    return bucket

def main():
    global _STOP
    ap = argparse.ArgumentParser(description="aitime layer-1 daemon")
    ap.add_argument("--repo", default="")
    ap.add_argument("--project", default=None)
    ap.add_argument("--server", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--bucket-secs", type=int, default=None,
                    help="override AITIME_BUCKET_SECS / gui bucket_mins (default 300)")
    ap.add_argument("--once", action="store_true", help="seal one bucket and exit")
    ap.add_argument("--flush-only", action="store_true", help="retry pending ledger upload and exit")
    a = ap.parse_args()

    rt = dcfg.resolve_runtime(
        cli_server=a.server, cli_token=a.token, cli_user=a.user,
        cli_repo=a.repo, cli_project=a.project or "",
    )
    if not rt["repo"]:
        print("repo required (--repo or ~/.config/struggletime/settings.json)", file=sys.stderr)
        return 1
    repo = Path(rt["repo"]).expanduser().resolve()
    if not repo.is_dir():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 1
    project = a.project or rt["project"] or repo.name
    server = rt["server"]
    user = rt["handle"]
    token = rt["token"]
    env_bucket = env("STRUGGLETIME_BUCKET_SECS", "AITIME_BUCKET_SECS", default="")
    if a.bucket_secs is not None:
        bucket_secs = a.bucket_secs
    elif env_bucket.isdigit():
        bucket_secs = int(env_bucket)
    else:
        bucket_secs = max(1, int(rt["bucket_mins"]) * 60)

    exec_log = Path(env("STRUGGLETIME_EXEC_LOG", "AITIME_EXEC_LOG", default=str(agent_evt.EXEC_LOG)))
    agent_log = Path(env("AITIME_AGENT_EVENTS", "STRUGGLETIME_AGENT_EVENTS", default=str(agent_evt.EVENT_LOG)))
    ledger = repo / ".aitime-ledger.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else {"prev_hash": GENESIS, "pending": [], "exec_offset": 0}
    state.setdefault("pending", [])
    state.setdefault("exec_offset", 0)
    state.setdefault("agent_offset", 0)
    state.setdefault("prev_hash", GENESIS)
    if "salt" not in state:
        state["salt"] = uuid.uuid4().hex
    gi = repo / ".gitignore"
    if (repo / ".git").exists() and (not gi.exists() or ".aitime-ledger.json" not in gi.read_text(errors="ignore")):
        open(gi, "a").write("\n.aitime-ledger.json\n")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except Exception:
            pass
    if hasattr(signal, "SIGUSR2"):
        try:
            signal.signal(signal.SIGUSR2, _on_signal)
        except Exception:
            pass

    was_paused = dcfg.is_paused()
    # Continue an existing server-side chain when this machine's ledger is fresh
    if server and (state.get("prev_hash") == GENESIS or not state.get("pending")):
        adopt_chain_head(state, server, token, project, ledger)
    flush_pending(state, server, user, token, ledger, project=project)
    if a.flush_only:
        return 0 if not state.get("pending") else 1

    bursts, counts, prev_snap, last_activity = {}, empty(), snapshot(repo), time.monotonic()
    file_meta, exec_categories, rel_langs = {}, {}, {}
    attr_hint = {}  # latest adapter/layer1 editor+agent hint for this bucket
    started_wall, started_mono = int(time.time()), time.monotonic()
    install_id = env("STRUGGLETIME_INSTALL", "AITIME_INSTALL", default="local")
    print(
        f"watching {repo} project={project} bucket={bucket_secs}s server={server or 'offline'}"
        f" os={host_os()['id']} agents={agent_log.name}+{exec_log.name}"
        f"{' PAUSED' if was_paused else ''}",
        flush=True,
    )
    while not _STOP:
        time.sleep(1)

        if os.environ.pop("_ST_FLUSH_NOW", None):
            flush_pending(state, server, user, token, ledger, project=project)

        paused_now = dcfg.is_paused()
        if paused_now != was_paused:
            print(f"{'paused — still watching, not sealing' if paused_now else 'resumed'}", flush=True)
            was_paused = paused_now
            if not paused_now:
                started_wall, started_mono = int(time.time()), time.monotonic()
                counts, bursts, file_meta, exec_categories, rel_langs, attr_hint = empty(), {}, {}, {}, {}, {}
        if paused_now:
            continue

        # Unified agent_events bridge first, then legacy exec.jsonl
        attr_hint = drain_event_log(agent_log, "agent_offset", state, counts, exec_categories, attr_hint)
        attr_hint = drain_event_log(exec_log, "exec_offset", state, counts, exec_categories, attr_hint)

        snap = snapshot(repo)
        ops = diff_ops(prev_snap, snap)
        if ops:
            last_activity = time.monotonic()
            n_touch = sum(1 for op in ops.values() if op != "deleted")
            kind = classify_burst(n_touch)
            editor, agent, source = default_attr(kind, attr_hint)
            if kind == "ai":
                counts["ai_writes"] += max(n_touch, 1)
            else:
                counts["human_edits"] += max(n_touch, 1)
            for rel, op in ops.items():
                bursts[rel] = bursts.get(rel, 0) + (0 if op == "deleted" else 1)
                lang = language_from_rel(rel)
                rel_langs[rel] = lang
                file_meta[rel] = {
                    "op": op,
                    "editor": editor,
                    "agent": agent,
                    "source": source,
                    "language": lang,
                }
        prev_snap = snap

        if time.monotonic() - started_mono < bucket_secs:
            continue
        counts["idle_secs"] = (
            int(max(0, bucket_secs - (last_activity - started_mono)))
            if time.monotonic() - last_activity > 60 else 0
        )
        bucket = seal_bucket(
            state, repo, project, bucket_secs, started_wall, started_mono, counts, bursts, install_id,
            file_meta=file_meta, exec_categories=exec_categories, rel_langs=rel_langs, attr_hint=attr_hint,
        )
        bursts, file_meta, exec_categories, rel_langs, attr_hint = {}, {}, {}, {}, {}
        save_ledger(ledger, state)
        print(
            f"bucket {bucket['bucket_id'][:8]} sealed {bucket['counts']} "
            f"editors={bucket.get('editors')} agents={bucket.get('agents')} "
            f"os={bucket.get('os')} languages={bucket.get('languages')}",
            flush=True,
        )
        flush_pending(state, server, user, token, ledger, project=project)
        if a.once:
            return 0
        counts, started_wall, started_mono = empty(), int(time.time()), time.monotonic()

    print("shutdown: flushing pending…", flush=True)
    flush_pending(state, server, user, token, ledger, project=project)
    save_ledger(ledger, state)
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
