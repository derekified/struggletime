#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layer-1 aitime daemon: multi-project polling watcher + agent event bridge.

Stdlib only, cross-platform (no native file watchers, no MCP). Metadata only —
raw paths never leave this process (anonymized file ids in the ledger salt).

Watches every linked project root from ~/.config/struggletime/settings.json
(and legacy ~/.aitime/gui.json) plus any CLI --repo flags. Drains
~/.aitime/agent_events.jsonl and legacy exec.jsonl on each poll tick; falls
back to process/workspace inference when no adapter fired. No journaling
required — file mtimes + running editors/agents are enough.
"""
import argparse, json, os, platform, signal, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
from verify import bucket_hash, anonymized_file_id, GENESIS, SILENT_ZERO_SECS, FORMULA_VERSION
import install_sig
import config as dcfg
import agent_event as agent_evt
import editors as edreg
import debugutil

# Directory basenames skipped while walking linked repos (cache / VCS / IDE noise).
IGNORE = {
    ".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".aitime",
    # Godot / Unity / Unreal
    ".godot", "Library", "Temp", "Obj", "Intermediate", "Saved", "Binaries",
    # JetBrains / Xcode
    ".idea", "DerivedData", "xcuserdata",
}
# Path segment names that always drop a file from tomography (nested caches).
IGNORE_SEGMENTS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".aitime",
    ".godot", "Library", "Temp", "Obj", "Intermediate", "Saved", "Binaries",
    ".idea", "DerivedData", "xcuserdata",
})
_STOP = False
SWITCH_QUIET_SECS = 30  # early-seal prior project after this quiet when another is active
FORMULA = FORMULA_VERSION

# Git plumbing + build/test cache markers (stat only — never read contents)
GIT_PULSE_RELS = (".git/index", ".git/HEAD", ".git/logs/HEAD", ".git/ORIG_HEAD", ".git/FETCH_HEAD")
CACHE_FILE_MARKERS = (
    ".pytest_cache/v/cache/lastfailed",
    ".pytest_cache/v/cache/nodeids",
    ".coverage",
    "tsconfig.tsbuildinfo",
    ".tsbuildinfo",
)
CACHE_DIR_MARKERS = (
    "node_modules/.cache",
    ".next/cache",
    "target/debug/.fingerprint",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    ".tox",
)

def ignored_dir_name(name):
    """True if an os.walk directory basename should be pruned."""
    if not name:
        return True
    if name in IGNORE:
        return True
    if name.endswith("-backups"):
        return True
    return False

def ignored_rel_path(rel):
    """True if a repo-relative path is cache/lock noise (never hash into buckets)."""
    if not rel:
        return True
    parts = Path(rel).parts
    for seg in parts:
        if seg in IGNORE_SEGMENTS:
            return True
        if seg.endswith("-backups"):
            return True
    name = parts[-1] if parts else ""
    low = name.lower()
    # Godot imports
    if low.endswith(".import"):
        return True
    # Blender autosaves
    if low.endswith(".blend1") or low.endswith(".blend2"):
        return True
    # KiCad / CAD locks and bak files
    if low == "fp-info-cache":
        return True
    if low.endswith("-bak"):
        return True
    if low.startswith("~") and low.endswith(".lck"):
        return True
    if low.endswith(".lck") and low.startswith("~"):
        return True
    return False

# Shell hook categories (name only — never arguments)
EXEC_CATS = {
    "git": "git",
    "pytest": "test_runner", "jest": "test_runner", "vitest": "test_runner", "test": "test_runner",
    "go": "test_runner", "cargo": "test_runner", "npm": "test_runner", "pnpm": "test_runner",
    "yarn": "test_runner", "make": "test_runner", "python": "test_runner", "python3": "test_runner", "node": "test_runner",
    "gcc": "compiler", "g++": "compiler", "clang": "compiler", "clang++": "compiler",
    "rustc": "compiler", "javac": "compiler", "tsc": "compiler",
    "prettier": "formatter", "eslint": "formatter", "black": "formatter", "gofmt": "formatter",
    "rustfmt": "formatter", "clang-format": "formatter", "ruff": "formatter", "autopep8": "formatter",
    "yapf": "formatter", "isort": "formatter", "biome": "formatter", "shfmt": "formatter",
    "claude": "ai_cli", "codex": "ai_cli", "aider": "ai_cli", "opencode": "ai_cli", "openclaw": "ai_cli",
    "gemini": "ai_cli", "goose": "ai_cli", "amp": "ai_cli", "crush": "ai_cli", "copilot": "ai_cli",
    "cursor-agent": "ai_cli", "qwen": "ai_cli", "cline": "ai_cli", "roo": "ai_cli",
}

FORMATTER_VERBS = {"fmt", "format", "fix", "mv", "rename"}

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
    """Rich tree snapshot: mtime_ns + size + inode (detects atomic agent write-renames)."""
    out = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if not ignored_dir_name(d)]
        for f in files:
            p = Path(root) / f
            try:
                rel = str(p.relative_to(repo))
            except ValueError:
                continue
            if ignored_rel_path(rel):
                continue
            try:
                st = p.stat()
                out[rel] = {
                    "mtime_ns": st.st_mtime_ns,
                    "size": int(st.st_size),
                    "ino": int(getattr(st, "st_ino", 0) or 0),
                }
            except Exception:
                pass
    return out

def _meta_mtime(meta):
    if isinstance(meta, dict):
        return meta.get("mtime_ns")
    return meta  # legacy int snapshot

def _stat_pulse(path):
    try:
        st = Path(path).stat()
        return (st.st_mtime_ns, int(st.st_size))
    except Exception:
        return None

def classify_name(name, verb=""):
    name = (name or "").lower().strip()
    verb = (verb or "").lower().strip()
    if verb in FORMATTER_VERBS and name in ("git", "cargo", "go", "npm", "pnpm", "yarn", "npx"):
        return "formatter"
    if name in ("go", "cargo") and verb in ("fmt", "format"):
        return "formatter"
    if name in EXEC_CATS:
        return EXEC_CATS[name]
    return "other"

def _clean_label(v, default="unknown"):
    s = str(v or default).lower().strip().replace(" ", "-")
    if not s or len(s) > 32 or "/" in s or "\\" in s or " " in s:
        return default
    return s

def _peek_cwd(raw):
    """Local-only routing hint from raw JSONL — never sealed into buckets."""
    if not isinstance(raw, dict):
        return ""
    for k in ("cwd", "workspace", "workdir", "working_directory"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def classify_exec(line):
    """Parse shell-hook / adapter / agent_events JSONL. Never keep args or paths."""
    e = json.loads(line)
    if not isinstance(e, dict):
        raise ValueError("exec line must be object")
    cwd = _peek_cwd(e)
    # Strip local routing keys before allowlist validation (cwd never enters buckets)
    scrubbed = {k: v for k, v in e.items() if k not in agent_evt.FORBIDDEN}
    if any(k in scrubbed for k in ("event_type", "editor", "agent", "files_touched_count", "project_id")):
        ok, cleaned = agent_evt.validate_event(scrubbed)
        if ok:
            cleaned["_cwd"] = cwd
            return cleaned
    verb = scrubbed.get("verb") or scrubbed.get("event_type") or ""
    if " " in verb or "/" in verb or len(verb) > 32:
        verb = ""
    cat = scrubbed.get("category") or classify_name(scrubbed.get("name") or scrubbed.get("cmd"), verb)
    if cat not in ("git", "test_runner", "compiler", "ai_cli", "other", "editor", "formatter"):
        cat = "other"
    return {
        "category": cat,
        "verb": verb,
        "event_type": _clean_label(scrubbed.get("event_type") or verb, "exec"),
        "exit_code": int(scrubbed.get("exit_code") or 0),
        "ts": scrubbed.get("ts"),
        "editor": _clean_label(scrubbed.get("editor"), "") or None,
        "agent": _clean_label(scrubbed.get("agent"), "") or None,
        "op": scrubbed.get("op") if scrubbed.get("op") in ("created", "modified", "deleted") else None,
        "files_touched_count": int(scrubbed.get("files_touched_count") or 0),
        "source": "adapter" if scrubbed.get("editor") or scrubbed.get("agent") or cat == "editor" else "layer1",
        "project_id": _clean_label(scrubbed.get("project_id"), "") or None,
        "_cwd": cwd,
    }

def empty():
    return {
        "human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0,
        "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
        "active_secs": 0, "first_event_ts": 0, "last_event_ts": 0,
        # Passive AI-struggle friction (all IDEs/agents; metadata counters only)
        "same_file_reedit_count": 0,
        "ai_retry_loops": 0,
        "failed_exec_count": 0,
        "test_fail_to_pass_transitions": 0,
        "agent_turn_churn": 0,
        "human_recovery_edits": 0,
        # Zero-hook struggle tomography (filesystem / git plumbing / caches)
        "atomic_agent_writes": 0,
        "git_plumbing_pulses": 0,
        "reflog_reverts": 0,
        "build_test_cycles": 0,
        "cache_fail_to_pass": 0,
    }

def _push_event(w, kind, ts, **extra):
    """Ordered micro-event log (local only; never sealed — drives counters)."""
    seq = w.setdefault("event_seq", [])
    row = {"t": int(ts), "k": kind}
    row.update({k: v for k, v in extra.items() if k in ("exit", "ino_flip", "fid")})
    seq.append(row)
    if len(seq) > 80:
        del seq[: len(seq) - 80]

def _track_reedits(w, touched_rels, kind):
    """Count overlapping re-edits / AI retry loops (rel paths stay local; sealed as file_id)."""
    prev = w.get("tick_files") or set()
    cur = set(touched_rels)
    seen = w.setdefault("touched_once", set())
    reedits = sum(1 for rel in cur if rel in seen)
    for rel in cur:
        seen.add(rel)
    if reedits:
        w["counts"]["same_file_reedit_count"] += reedits
    # Retry loop: same files touched again after a prior tick, especially under AI/fail pressure
    if (prev & cur) and (
        kind == "ai"
        or w["counts"].get("ai_writes", 0) > 0
        or w.get("saw_fail")
        or w.get("open_fail")
    ):
        w["counts"]["ai_retry_loops"] += 1
    w["tick_files"] = cur

def _track_human_recovery(w, n_touch, kind):
    """1–2 file human edits after an AI burst or failed run count as recovery."""
    if kind != "human":
        return
    if n_touch < 1 or n_touch > 2:
        return
    if (
        w["counts"].get("ai_writes", 0) > 0
        or w.get("saw_fail")
        or w.get("open_fail")
        or w["counts"].get("failed_exec_count", 0) > 0
    ):
        w["counts"]["human_recovery_edits"] += 1

def classify_edit_batch(n_touch, *, edit_ticks, lines_hint, dt_sec, recent_cats, attr_hint):
    """Fuse velocity/cadence/exec class — never 5+ files ⇒ AI by default."""
    hint = attr_hint or {}
    cats = recent_cats or {}
    if int(cats.get("formatter", 0) or 0) > 0:
        return "human"
    agent = (hint.get("agent") or "").lower()
    if agent and agent not in ("human", "unknown", "unknown-agent"):
        return "ai"
    if hint.get("source") == "adapter" and agent == "human":
        return "human"
    dt = max(float(dt_sec or 0.25), 0.25)
    velocity = max(0, int(lines_hint or 0)) / dt
    bulk_single_tick = n_touch >= 5 and edit_ticks <= 1
    no_verify = not any(int(cats.get(k, 0) or 0) for k in ("test_runner", "compiler"))
    if bulk_single_tick and velocity >= 40 and no_verify:
        return "ai"
    if bulk_single_tick and int(lines_hint or 0) >= 200 and no_verify:
        return "ai"
    return "human"

def detect_running_editors():
    return edreg.detect_running_editors()

def detect_running_agents():
    return edreg.detect_running_agents()

def host_os():
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
    ext = Path(rel or "").suffix.lower()
    return LANG_EXT.get(ext) or "other"

def default_attr(burst_kind="human", hint=None):
    editor, agent, _kind, source = edreg.classify_active(burst_kind, hint)
    return editor, agent, source

def diff_ops(prev_snap, snap):
    ops = {}
    for rel, meta in snap.items():
        if rel not in prev_snap:
            ops[rel] = "created"
            continue
        old = prev_snap[rel]
        if isinstance(old, dict) and isinstance(meta, dict):
            if (
                old.get("mtime_ns") != meta.get("mtime_ns")
                or old.get("size") != meta.get("size")
                or old.get("ino") != meta.get("ino")
            ):
                ops[rel] = "modified"
        elif _meta_mtime(old) != _meta_mtime(meta):
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
            if e.code in (400, 401, 403, 409, 413, 422):
                return False, f"http {e.code}: {raw}"
            last_err = f"http {e.code}: {raw}"
        except Exception as e:
            last_err = str(e)
        time.sleep(min(2 ** attempt, 8))
    return False, last_err or "upload failed"

def fetch_chain_head(server, token, project):
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

def fetch_remote_projects(server, token):
    """Optional: pull local_path mappings from the API (passive, no journaling)."""
    if not server or not token:
        return []
    url = server.rstrip("/") + "/v1/projects"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    out = []
    for p in data.get("projects") or []:
        if p.get("deleted") or p.get("archived"):
            continue
        path = (p.get("local_path") or "").strip()
        pid = (p.get("id") or "").strip()
        if path and pid and Path(path).expanduser().is_dir():
            out.append({"id": pid, "path": path})
    return out

def _rehash_pending_from(state, prev_hash):
    """Rewrite pending window so bucket[0].prev_hash == prev_hash and hashes chain."""
    pending = state.get("pending") or []
    if not pending:
        state["prev_hash"] = prev_hash
        return
    loaded = install_sig.load_or_create_keypair(dcfg.CONFIG_HOME)
    out = []
    tip = prev_hash
    for b in pending:
        nxt = dict(b)
        nxt["prev_hash"] = tip
        nxt.pop("signature", None)
        nxt["hash"] = bucket_hash(nxt)
        if loaded:
            nxt["install_id"] = loaded[3]
            nxt["install_pubkey"] = install_sig._b64(loaded[2])
            nxt["sig_alg"] = install_sig.ALG
            nxt["signature"] = install_sig.sign_hash(nxt["hash"], loaded[0])
        out.append(nxt)
        tip = nxt["hash"]
    state["pending"] = out
    state["prev_hash"] = tip

def adopt_chain_head(state, server, token, project, ledger=None):
    head = fetch_chain_head(server, token, project)
    if head is None:
        return False
    if state.get("prev_hash") == head and not state.get("needs_rebase"):
        return False
    pending = state.get("pending") or []
    first_prev = pending[0].get("prev_hash") if pending else None
    if pending and first_prev not in (GENESIS, head):
        # Cap/drop left a broken link — rebase kept window onto server head
        _rehash_pending_from(state, head)
        state["needs_rebase"] = False
        if ledger is not None:
            save_ledger(ledger, state)
        print(f"rebased {len(state['pending'])} pending bucket(s) onto server head for {project}", flush=True)
        return True
    if pending and first_prev in (GENESIS, head) and state.get("needs_rebase"):
        _rehash_pending_from(state, head)
        state["needs_rebase"] = False
        if ledger is not None:
            save_ledger(ledger, state)
        print(f"rebased {len(state['pending'])} pending bucket(s) onto server head for {project}", flush=True)
        return True
    dropped = len(pending)
    state["pending"] = []
    state["prev_hash"] = head
    state["needs_rebase"] = False
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

def flush_pending(state, server, user, token, ledger, project=None, chunk_size=40):
    """Upload pending buckets in small chunks so the server never OOM-reads a multi‑MB POST."""
    if not state.get("pending"):
        return False
    if not server:
        print(f"offline: {len(state['pending'])} bucket(s) kept in ledger for {project or '?'}", flush=True)
        save_ledger(ledger, state)
        return False
    if not token:
        print(
            f"sync deferred ({len(state['pending'])} pending) for {project or '?'}: "
            f"no API token — set via struggletime-helper apikey <st_…> (ledger kept)",
            flush=True,
        )
        save_ledger(ledger, state)
        return False
    total = 0
    while state.get("pending"):
        chunk = state["pending"][:chunk_size]
        ok, msg = post_buckets(server, user, token, chunk)
        if not ok:
            print(f"sync deferred ({len(state['pending'])} pending) for {project or '?'}: {msg}", flush=True)
            save_ledger(ledger, state)
            if "prev_hash mismatch" in (msg or "") and project:
                if adopt_chain_head(state, server, token, project, ledger):
                    return False
            if "413" in (msg or "") or "too large" in (msg or "") or "too many buckets" in (msg or ""):
                if chunk_size > 1:
                    return flush_pending(state, server, user, token, ledger, project=project, chunk_size=max(1, chunk_size // 2))
            if "401" in (msg or "") or "403" in (msg or ""):
                print("hint: API token rejected — refresh with struggletime-helper apikey <st_…>", flush=True)
            return False
        state["pending"] = state["pending"][len(chunk):]
        total += len(chunk)
        save_ledger(ledger, state)
    print(f"synced {total} bucket(s) for {project or '?'}", flush=True)
    return True

def _on_signal(signum, _frame):
    global _STOP
    if signum in (signal.SIGTERM, signal.SIGINT):
        print(f"signal {signum}: shutting down…", flush=True)
        _STOP = True
    elif hasattr(signal, "SIGUSR2") and signum == signal.SIGUSR2:
        os.environ["_ST_FLUSH_NOW"] = "1"

def match_project(cwd_or_path, watchers):
    """Longest-prefix match of a local path onto a watched project root."""
    if not cwd_or_path:
        return None
    try:
        target = str(Path(cwd_or_path).expanduser().resolve())
    except Exception:
        target = str(Path(cwd_or_path).expanduser())
    best, best_len = None, -1
    for w in watchers:
        root = w["root_s"]
        if target == root or target.startswith(root + os.sep):
            if len(root) > best_len:
                best, best_len = w, len(root)
    return best

def infer_project_from_processes(watchers):
    blob = edreg.process_blob()
    best, best_len = None, -1
    for w in watchers:
        needle = w["root_s"].lower()
        if needle and needle in blob and len(needle) > best_len:
            best, best_len = w, len(needle)
    return best

def note_activity(w, now_wall, now_mono):
    if not w["started_mono"]:
        w["started_wall"] = now_wall
        w["started_mono"] = now_mono
    if not w.get("first_activity_mono"):
        w["first_activity_mono"] = now_mono
    if not w["counts"]["first_event_ts"]:
        w["counts"]["first_event_ts"] = now_wall
    w["counts"]["last_event_ts"] = max(int(w["counts"].get("last_event_ts") or 0), int(now_wall))
    w["last_activity"] = now_mono
    w["had_activity"] = True
    w["silent_followup"] = 0

def _acc_label(w, key, label):
    """Accumulate editor/agent labels across ticks (last human recovery must not erase AI)."""
    lab = _clean_label(label, "")
    if not lab:
        return
    bag = w.setdefault(key, {})
    bag[lab] = bag.get(lab, 0) + 1

def apply_event_to_watcher(w, e, now_wall, now_mono):
    counts = w["counts"]
    exec_categories = w["exec_categories"]
    counts["executions"] += 1
    cat = e.get("category") or "other"
    exec_categories[cat] = exec_categories.get(cat, 0) + 1
    exit_code = int(e.get("exit_code") or 0)
    # Any non-zero exit opens a struggle window (hooks often omit name → category=other)
    if exit_code:
        counts["exec_failures"] += 1
        counts["failed_exec_count"] += 1
        w["saw_fail"] = True
        w["open_fail"] = True  # survives bucket seal until a matching pass
    elif (w.get("saw_fail") or w.get("open_fail")) and cat in (
        "test_runner", "compiler", "other", "ai_cli",
    ):
        # Fail→pass recovery — include category=other (common when hook omits command name)
        w["saw_pass_after_fail"] = True
        counts["test_fail_to_pass_transitions"] += 1
        w["saw_fail"] = False
        w["open_fail"] = False
    if cat == "ai_cli":
        counts["prompts"] += 1
        counts["agent_turn_churn"] += 1
        ag = e.get("agent") or _clean_label(e.get("verb") or "ai-cli", "unknown-agent")
        ed = e.get("editor") or w["attr_hint"].get("editor")
        w["attr_hint"] = {
            "agent": ag,
            "editor": ed,
            "source": "adapter",
        }
        _acc_label(w, "agents_acc", ag)
        _acc_label(w, "editors_acc", ed)
        _push_event(w, "agent_turn", now_wall, exit=exit_code)
    if e.get("event_type") in ("tool", "prompt", "start") or (cat == "ai_cli" and e.get("verb")):
        counts["agent_turn_churn"] = max(counts["agent_turn_churn"], counts["prompts"])
        _push_event(w, "tool_call" if e.get("event_type") == "tool" else "agent_turn", now_wall, exit=exit_code)
    if cat in ("test_runner", "compiler", "other", "git", "formatter") and e.get("event_type") in ("exec", "tool", ""):
        _push_event(w, "cmd_exec", now_wall, exit=exit_code)
    if cat == "editor":
        eds = detect_running_editors()
        ed = e.get("editor") or (eds[0] if eds else "unknown")
        ag = e.get("agent") or "human"
        w["attr_hint"] = {
            "editor": ed,
            "agent": ag,
            "source": "adapter",
        }
        _acc_label(w, "editors_acc", ed)
        _acc_label(w, "agents_acc", ag)
    if cat == "git" and e.get("verb") == "commit":
        counts["commits"] += 1
    n = int(e.get("files_touched_count") or 0)
    if n >= 5 and cat in ("editor", "ai_cli"):
        # Adapter-reported bulk touch — still require agent signal, not file count alone
        agents = detect_running_agents()
        if e.get("agent") or agents:
            counts["ai_writes"] += max(1, n // 5)
            w["attr_hint"] = {
                **w["attr_hint"],
                "agent": e.get("agent") or agents[0],
                "source": w["attr_hint"].get("source") or "adapter",
            }
    # Adapter re-edit / recovery under failure or AI pressure (paths never stored)
    under_pressure = bool(w.get("saw_fail") or w.get("open_fail") or counts.get("ai_writes", 0) > 0)
    if n > 0 and cat in ("editor", "ai_cli") and under_pressure:
        agent = (e.get("agent") or "human")
        if n <= 2 and agent == "human":
            counts["human_recovery_edits"] += 1
            counts["same_file_reedit_count"] += 1
        else:
            # AI IDE agent re-touch after fail (was previously dropped when ai_writes==0)
            counts["ai_retry_loops"] += 1
            counts["same_file_reedit_count"] += max(1, min(n, 5))
            if agent not in ("human", "unknown", "") and counts.get("ai_writes", 0) == 0:
                counts["ai_writes"] += 1
    note_activity(w, now_wall, now_mono)

def seal_bucket(w, bucket_secs, install_id):
    state, repo, project = w["state"], w["repo"], w["project"]
    counts = dict(w["counts"])
    bursts, file_meta = w["bursts"], w["file_meta"]
    exec_categories, rel_langs, attr_hint = w["exec_categories"], w["rel_langs"], w["attr_hint"]
    started_wall, started_mono = w["started_wall"], w["started_mono"]
    now_mono = time.monotonic()
    now_wall = int(time.time())

    # Active-span: prefer monotonic clock (wall ts can collapse same-second events to 0)
    warm = max(0, min(60, int(now_mono - w["last_activity"]))) if w.get("had_activity") else 0
    if w.get("had_activity") and w.get("first_activity_mono"):
        counts["active_secs"] = max(1, min(int(bucket_secs), int(now_mono - w["first_activity_mono"]) + warm))
    elif w.get("had_activity") and started_mono:
        counts["active_secs"] = max(1, min(int(bucket_secs), int(now_mono - started_mono)))
    else:
        first_ts = int(counts.get("first_event_ts") or 0)
        last_ts = int(counts.get("last_event_ts") or 0)
        if first_ts and last_ts and last_ts >= first_ts:
            counts["active_secs"] = max(1, min(int(bucket_secs), (last_ts - first_ts) + warm))
        else:
            counts["active_secs"] = 0
    idle_tail = int(max(0, now_mono - w["last_activity"])) if w.get("had_activity") else int(max(0, now_mono - started_mono))
    counts["idle_secs"] = min(int(bucket_secs), idle_tail) if idle_tail > 60 or not w.get("had_activity") else 0
    # Sequence-derived: cache fail→pass already counted; merge into test_fail_to_pass if needed
    if counts.get("cache_fail_to_pass", 0) and not counts.get("test_fail_to_pass_transitions"):
        counts["test_fail_to_pass_transitions"] = int(counts["cache_fail_to_pass"])
    if w.get("saw_pass_after_fail") and counts["exec_failures"] == 0:
        counts["exec_failures"] = max(1, counts["exec_failures"])
    if w.get("saw_pass_after_fail") and counts.get("test_fail_to_pass_transitions", 0) == 0:
        counts["test_fail_to_pass_transitions"] = 1
    counts["failed_exec_count"] = max(int(counts.get("failed_exec_count") or 0), int(counts.get("exec_failures") or 0))

    anchor, files = git_anchor(repo, state["salt"], bursts, file_meta=file_meta)
    lines = anchor["lines_added"] + anchor["lines_deleted"]
    # Reclassify only on true bulk velocity with no verification — not raw line totals alone
    if (
        lines >= 200
        and counts["human_edits"] > 0
        and counts["ai_writes"] == 0
        and w["edit_ticks"] <= 1
        and not int(exec_categories.get("formatter", 0) or 0)
        and not int(exec_categories.get("test_runner", 0) or 0)
        and not int(exec_categories.get("compiler", 0) or 0)
    ):
        counts = dict(counts)
        counts["ai_writes"] += counts["human_edits"]
        counts["human_edits"] = 0
        for meta in file_meta.values():
            if meta.get("agent") == "human":
                meta["agent"] = (detect_running_agents() or ["unknown-agent"])[0]

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
    # Window-level attribution: keep AI agent/editor even if last tick was human recovery
    for ed, n in (w.get("editors_acc") or {}).items():
        editors[ed] = editors.get(ed, 0) + n
    for ag, n in (w.get("agents_acc") or {}).items():
        agents[ag] = agents.get(ag, 0) + n
    if attr_hint.get("editor"):
        ed = _clean_label(attr_hint["editor"], "unknown")
        editors[ed] = editors.get(ed, 0) + 1
    if attr_hint.get("agent"):
        ag = _clean_label(attr_hint["agent"], "human")
        agents[ag] = agents.get(ag, 0) + 1
    if not editors:
        ed, ag, _ = default_attr("human", attr_hint)
        editors[ed] = 1
        if ag and ag not in agents:
            agents[ag] = agents.get(ag, 0) + 1
    if not languages:
        languages["other"] = 1
    os_info = host_os()
    mono_ms = int((now_mono - started_mono) * 1000) if started_mono else int(bucket_secs * 1000)
    bucket = {
        "v": 1, "bucket_id": str(uuid.uuid4()), "install_id": install_id,
        "project_id": project, "repo_remote_hash": None, "bucket_start_wall": started_wall or now_wall,
        "bucket_secs": max(1, min(int(bucket_secs), max(1, mono_ms // 1000))),
        "mono_delta_ms": max(0, mono_ms),
        "counts": counts, "git": anchor, "files": files,
        "exec_categories": exec_categories or {},
        "editors": editors, "agents": agents, "languages": languages,
        "os": os_info["id"], "os_detail": os_info,
        "formula_version": FORMULA, "prev_hash": state["prev_hash"],
    }
    # Prefer cryptographic install_id from Ed25519 pubkey when keys exist
    loaded = install_sig.load_or_create_keypair(dcfg.CONFIG_HOME)
    if loaded:
        _priv, _pub, pub_raw, iid = loaded
        bucket["install_id"] = iid
        bucket["install_pubkey"] = install_sig._b64(pub_raw)
        bucket["sig_alg"] = install_sig.ALG
    bucket["hash"] = bucket_hash(bucket)
    if loaded:
        sig = install_sig.sign_hash(bucket["hash"], loaded[0])
        bucket["signature"] = sig
    else:
        bucket["signature"] = None
    state["prev_hash"] = bucket["hash"]
    state.setdefault("pending", []).append(bucket)
    # Hard cap — unbounded pending (stress tests) produced 30MB+ POSTs that OOM'd the API
    max_pending = int(os.environ.get("STRUGGLETIME_MAX_PENDING", "80"))
    if len(state["pending"]) > max_pending:
        drop = len(state["pending"]) - max_pending
        state["pending"] = state["pending"][drop:]
        # Dropping oldest breaks prev_hash continuity — mark for server-head rebase
        state["needs_rebase"] = True
        print(f"pending capped at {max_pending} (dropped {drop} oldest; rebase on next sync)", flush=True)
    return bucket

def reset_window(w, now_wall=None, now_mono=None, keep_alive=False, after_active=False):
    prior_silent = int(w.get("silent_followup") or 0)
    # Cross-bucket struggle continuity: open fail + recent files survive the seal
    carry_fail = bool(w.get("open_fail") or (w.get("saw_fail") and not w.get("saw_pass_after_fail")))
    carry_files = set(w.get("touched_once") or set()) if (carry_fail or after_active) else set()
    carry_hint = dict(w.get("attr_hint") or {}) if carry_fail else {}
    w["bursts"] = {}
    w["counts"] = empty()
    w["file_meta"] = {}
    w["exec_categories"] = {}
    w["rel_langs"] = {}
    w["attr_hint"] = carry_hint
    w["edit_ticks"] = 0
    w["saw_fail"] = carry_fail
    w["open_fail"] = carry_fail
    w["saw_pass_after_fail"] = False
    w["had_activity"] = False
    w["tick_files"] = set(carry_files)
    w["touched_once"] = set(carry_files)
    w["editors_acc"] = {}
    w["agents_acc"] = {}
    w["event_seq"] = []
    w["first_activity_mono"] = None
    if keep_alive:
        w["started_wall"] = now_wall or int(time.time())
        w["started_mono"] = now_mono or time.monotonic()
        w["last_activity"] = w["started_mono"]
        # After real work, start/continue the 5–12m silent-debug follow-up window
        w["silent_followup"] = 0 if after_active else prior_silent
    else:
        w["started_wall"] = None
        w["started_mono"] = None
        w["last_activity"] = time.monotonic()
        w["silent_followup"] = 0
        if not carry_fail:
            w["tick_files"] = set()
            w["touched_once"] = set()
            w["saw_fail"] = False
            w["open_fail"] = False

def ensure_gitignore(repo):
    gi = repo / ".gitignore"
    if (repo / ".git").exists() and (not gi.exists() or ".aitime-ledger.json" not in gi.read_text(errors="ignore")):
        with open(gi, "a") as fh:
            fh.write("\n.aitime-ledger.json\n")

def load_ledger(repo):
    ledger = repo / ".aitime-ledger.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else {"prev_hash": GENESIS, "pending": [], "exec_offset": 0}
    state.setdefault("pending", [])
    state.setdefault("exec_offset", 0)
    state.setdefault("agent_offset", 0)
    state.setdefault("prev_hash", GENESIS)
    if "salt" not in state:
        state["salt"] = uuid.uuid4().hex
    return ledger, state

def _baseline_pulses(repo):
    """Seed git/cache pulse maps so the first poll doesn't fake a struggle storm."""
    git_pulse = {rel: _stat_pulse(repo / rel) for rel in GIT_PULSE_RELS}
    cache_pulse = {rel: _stat_pulse(repo / rel) for rel in CACHE_FILE_MARKERS}
    for rel in CACHE_DIR_MARKERS:
        cache_pulse["dir:" + rel] = _stat_pulse(repo / rel)
    return git_pulse, cache_pulse

def make_watcher(path, project_id):
    repo = Path(path).expanduser().resolve()
    project = project_id or repo.name
    ledger, state = load_ledger(repo)
    ensure_gitignore(repo)
    git_pulse, cache_pulse = _baseline_pulses(repo)
    return {
        "repo": repo,
        "root_s": str(repo),
        "project": project,
        "ledger": ledger,
        "state": state,
        "prev_snap": snapshot(repo),
        "bursts": {},
        "counts": empty(),
        "file_meta": {},
        "exec_categories": {},
        "rel_langs": {},
        "attr_hint": {},
        "edit_ticks": 0,
        "saw_fail": False,
        "open_fail": False,
        "saw_pass_after_fail": False,
        "had_activity": False,
        "tick_files": set(),
        "touched_once": set(),
        "editors_acc": {},
        "agents_acc": {},
        "event_seq": [],
        "first_activity_mono": None,
        "git_pulse": git_pulse,
        "cache_pulse": cache_pulse,
        "started_wall": None,
        "started_mono": None,
        "last_activity": time.monotonic(),
        "silent_followup": 0,
    }

def resolve_watch_list(cli_repos, project_maps, cli_project, rt):
    """Merge settings projects + CLI --repo/--project-map into concrete watchers.

    When CLI passes --repo, only those roots are watched (settings/remote fill
    missing project ids for matching paths — they must not add extra roots that
    steal seals or break hash chains).
    """
    remote = fetch_remote_projects(rt.get("server"), rt.get("token")) if rt.get("server") else []
    if cli_repos:
        rows = []
        for path in cli_repos:
            pid = project_maps.get(str(Path(path).expanduser())) or project_maps.get(path) or ""
            rows.append({"id": pid, "path": path})
        enrich = list(rt.get("projects") or []) + list(remote)
    else:
        rows = list(rt.get("projects") or [])
        if not rows and rt.get("repo"):
            rows.append({"id": cli_project or rt.get("project") or "", "path": rt["repo"]})
        enrich = list(remote)
        rows = rows + list(remote)

    # path → project id from settings + API (longest / last non-empty wins per path)
    enrich_ids = {}
    for row in enrich:
        path = row.get("path") or ""
        pid = (row.get("id") or "").strip()
        if not path or not pid:
            continue
        try:
            enrich_ids[str(Path(path).expanduser().resolve())] = pid
        except Exception:
            enrich_ids[path] = pid

    watchers, seen = [], set()
    skipped = []
    for row in rows:
        path = row.get("path") or ""
        try:
            repo = Path(path).expanduser().resolve()
        except Exception:
            skipped.append(path or "?")
            continue
        if not repo.is_dir():
            skipped.append(str(path))
            continue
        key = str(repo)
        if key in seen:
            if row.get("id"):
                for w in watchers:
                    if w["root_s"] == key and (not w["project"] or w["project"] == repo.name):
                        w["project"] = row["id"]
            continue
        seen.add(key)
        pid = row.get("id") or ""
        if key in project_maps:
            pid = project_maps[key] or pid
        for mk, mv in project_maps.items():
            try:
                if str(Path(mk).expanduser().resolve()) == key and mv:
                    pid = mv
            except Exception:
                pass
        if not pid:
            pid = enrich_ids.get(key) or ""
        if not pid and cli_project and len(cli_repos) == 1:
            try:
                cli_key = str(Path(cli_repos[0]).expanduser().resolve())
            except Exception:
                cli_key = ""
            if key == cli_key:
                pid = cli_project
        watchers.append(make_watcher(repo, pid))
    if skipped:
        print(
            f"skipping {len(skipped)} missing project path(s): "
            + ", ".join(skipped[:5])
            + ("…" if len(skipped) > 5 else ""),
            flush=True,
        )
    return watchers

def drain_shared_logs(watchers, agent_log, exec_log, shared_state):
    """Drain global event logs once; route each line to the matching project."""
    now_wall, now_mono = int(time.time()), time.monotonic()
    fallback = infer_project_from_processes(watchers)

    def drain_one(path, offset_key):
        path = Path(path)
        if not path.exists():
            return
        try:
            lines = path.read_text().splitlines()
        except Exception:
            return
        start = int(shared_state.get(offset_key) or 0)
        for line in lines[start:]:
            try:
                e = classify_exec(line)
            except Exception:
                continue
            debugutil.debug_log(
                "drain",
                editor=e.get("editor"),
                agent=e.get("agent"),
                event_type=e.get("event_type"),
                category=e.get("category"),
                files_touched_count=e.get("files_touched_count"),
                source=e.get("source"),
            )
            w = None
            if e.get("project_id"):
                for cand in watchers:
                    if cand["project"] == e["project_id"]:
                        w = cand
                        break
            if w is None:
                w = match_project(e.get("_cwd"), watchers)
            if w is None:
                w = fallback
            if w is None and len(watchers) == 1:
                w = watchers[0]
            if w is None:
                continue
            apply_event_to_watcher(w, e, now_wall, now_mono)
            # Mirror offsets into per-repo ledgers for crash recovery of shared cursors
            w["state"][offset_key] = len(lines)
        shared_state[offset_key] = len(lines)

    drain_one(agent_log, "agent_offset")
    drain_one(exec_log, "exec_offset")

def poll_tomography(w):
    """Zero-hook struggle tomography: git plumbing + build/test cache footprints."""
    now_wall, now_mono = int(time.time()), time.monotonic()
    repo = w["repo"]
    counts = w["counts"]
    changed = False

    git_prev = w.setdefault("git_pulse", {})
    for rel in GIT_PULSE_RELS:
        cur = _stat_pulse(repo / rel)
        old = git_prev.get(rel)
        if cur is not None and cur != old:
            if old is not None:
                counts["git_plumbing_pulses"] += 1
                changed = True
                _push_event(w, "git_pulse", now_wall)
                if rel in (".git/ORIG_HEAD", ".git/logs/HEAD") and old[1] != cur[1]:
                    # reflog / ORIG_HEAD churn often means revert/checkout thrash
                    if rel == ".git/ORIG_HEAD":
                        counts["reflog_reverts"] += 1
            git_prev[rel] = cur
        elif cur is None and rel in git_prev:
            git_prev[rel] = None

    cache_prev = w.setdefault("cache_pulse", {})
    for rel in CACHE_FILE_MARKERS:
        cur = _stat_pulse(repo / rel)
        old = cache_prev.get(rel)
        if cur is not None and cur != old:
            if old is not None:
                counts["build_test_cycles"] += 1
                changed = True
                _push_event(w, "cache_pulse", now_wall)
                # lastfailed: size>0 → fail pressure; collapse to 0 → fail-to-pass
                if rel.endswith("lastfailed"):
                    old_sz, new_sz = old[1], cur[1]
                    if new_sz > 0:
                        counts["failed_exec_count"] += 1
                        w["saw_fail"] = True
                        w["open_fail"] = True
                    elif old_sz > 0 and new_sz == 0:
                        counts["cache_fail_to_pass"] += 1
                        counts["test_fail_to_pass_transitions"] += 1
                        w["saw_pass_after_fail"] = True
                        w["saw_fail"] = False
                        w["open_fail"] = False
            else:
                # first appearance of lastfailed with size>0
                if rel.endswith("lastfailed") and cur[1] > 0:
                    counts["build_test_cycles"] += 1
                    counts["failed_exec_count"] += 1
                    w["saw_fail"] = True
                    w["open_fail"] = True
                    changed = True
            cache_prev[rel] = cur
        elif cur is None and old is not None:
            if rel.endswith("lastfailed") and old[1] > 0:
                counts["cache_fail_to_pass"] += 1
                counts["test_fail_to_pass_transitions"] += 1
                w["saw_pass_after_fail"] = True
                w["saw_fail"] = False
                w["open_fail"] = False
                changed = True
            cache_prev[rel] = None

    for rel in CACHE_DIR_MARKERS:
        cur = _stat_pulse(repo / rel)
        old = cache_prev.get("dir:" + rel)
        if cur is not None and cur != old:
            if old is not None:
                counts["build_test_cycles"] += 1
                changed = True
                _push_event(w, "cache_dir", now_wall)
            cache_prev["dir:" + rel] = cur

    if changed:
        note_activity(w, now_wall, now_mono)
        # Tomography alone is enough to keep the window alive for struggle scoring
        if not counts.get("human_edits") and not counts.get("ai_writes") and not counts.get("executions"):
            # Attribute a minimal human_edits so _has_activity / sealing bill time
            # Prefer process editor; do not invent edits when only git status ran once
            if counts.get("build_test_cycles", 0) or counts.get("cache_fail_to_pass", 0):
                counts["executions"] = max(1, int(counts.get("executions") or 0))

def poll_repo_files(w, bucket_secs):
    now_wall, now_mono = int(time.time()), time.monotonic()
    snap = snapshot(w["repo"])
    ops = diff_ops(w["prev_snap"], snap)
    if ops:
        note_activity(w, now_wall, now_mono)
        w["edit_ticks"] += 1
        n_touch = sum(1 for op in ops.values() if op != "deleted")
        lines_hint = 0
        try:
            for line in git(w["repo"], "diff", "--numstat").splitlines():
                p = line.split("\t")
                if len(p) >= 2:
                    lines_hint += (int(p[0]) if p[0].isdigit() else 0) + (int(p[1]) if p[1].isdigit() else 0)
        except Exception:
            lines_hint = 0
        if lines_hint < n_touch * 5:
            lines_hint = max(lines_hint, n_touch * 50)
        kind = classify_edit_batch(
            n_touch,
            edit_ticks=w["edit_ticks"],
            lines_hint=lines_hint,
            dt_sec=1.0,
            recent_cats=w["exec_categories"],
            attr_hint=w["attr_hint"],
        )
        running_agents = detect_running_agents()
        running_editors = detect_running_editors()
        # Inode flip (atomic write-rename) is a strong agent-write signal
        inode_flips = 0
        for rel, op in ops.items():
            if op == "deleted":
                continue
            old, new = w["prev_snap"].get(rel), snap.get(rel)
            if (
                isinstance(old, dict) and isinstance(new, dict)
                and old.get("ino") and new.get("ino")
                and old["ino"] != new["ino"]
            ):
                inode_flips += 1
        if inode_flips:
            w["counts"]["atomic_agent_writes"] += inode_flips
            _push_event(w, "inode_flip", now_wall, ino_flip=inode_flips)
            if kind == "human" and (inode_flips >= 2 or running_agents or w.get("open_fail")):
                kind = "ai"
        if kind == "human" and running_agents and (
            w["edit_ticks"] > 1 or n_touch >= 3 or w.get("saw_fail") or w.get("open_fail")
        ):
            kind = "ai"
        editor, agent, source = default_attr(kind, w["attr_hint"])
        touched = [rel for rel, op in ops.items() if op != "deleted"]
        _track_reedits(w, touched, kind)
        _track_human_recovery(w, n_touch, kind)
        _push_event(w, "file_change", now_wall)
        if kind == "ai":
            w["counts"]["ai_writes"] += max(n_touch, 1)
            if running_agents or (w["attr_hint"].get("agent") not in (None, "", "human")):
                w["counts"]["agent_turn_churn"] += 1
        else:
            w["counts"]["human_edits"] += max(n_touch, 1)
        _acc_label(w, "editors_acc", editor)
        _acc_label(w, "agents_acc", agent)
        for rel, op in ops.items():
            w["bursts"][rel] = w["bursts"].get(rel, 0) + (0 if op == "deleted" else 1)
            lang = language_from_rel(rel)
            w["rel_langs"][rel] = lang
            w["file_meta"][rel] = {
                "op": op, "editor": editor, "agent": agent, "source": source, "language": lang,
            }
        if running_editors and not w["attr_hint"].get("editor"):
            w["attr_hint"] = {
                **w["attr_hint"],
                "editor": running_editors[0],
                "source": w["attr_hint"].get("source") or "layer1",
            }
        if running_agents and kind == "ai" and (
            not w["attr_hint"].get("agent") or w["attr_hint"].get("agent") == "human"
        ):
            w["attr_hint"] = {
                **w["attr_hint"],
                "agent": running_agents[0],
                "source": w["attr_hint"].get("source") or "layer1",
            }
    w["prev_snap"] = snap
    poll_tomography(w)

def maybe_seal(w, bucket_secs, install_id, server, user, token, force=False, reason=""):
    if not w["started_mono"]:
        return None
    now_mono = time.monotonic()
    elapsed = now_mono - w["started_mono"]
    if not force and elapsed < bucket_secs:
        return None
    was_active = bool(w["had_activity"])
    # Drop pure-AFK follow-up once quiet credit has fully decayed (>12m)
    if not was_active and not force:
        upcoming = int(w.get("silent_followup") or 0) + int(elapsed)
        if upcoming > SILENT_ZERO_SECS:
            reset_window(w)
            return None
    bucket = seal_bucket(w, bucket_secs if not force else max(1, int(elapsed)), install_id)
    save_ledger(w["ledger"], w["state"])
    print(
        f"bucket {bucket['bucket_id'][:8]} project={w['project']} sealed {bucket['counts']} "
        f"editors={bucket.get('editors')} agents={bucket.get('agents')} "
        f"os={bucket.get('os')} languages={bucket.get('languages')}"
        + (f" ({reason})" if reason else ""),
        flush=True,
    )
    flush_pending(w["state"], server, user, token, w["ledger"], project=w["project"])
    if was_active:
        reset_window(w, keep_alive=True, after_active=True)
    else:
        w["silent_followup"] = int(w.get("silent_followup") or 0) + int(bucket.get("bucket_secs") or elapsed)
        keep = w["silent_followup"] < SILENT_ZERO_SECS
        reset_window(w, keep_alive=keep, after_active=False)
        if keep:
            w["silent_followup"] = int(w.get("silent_followup") or 0)
    return bucket

def handle_project_switch(watchers, bucket_secs, install_id, server, user, token):
    """If activity moved to another project, early-seal the quiet prior window."""
    active = [w for w in watchers if w["had_activity"] and w["started_mono"]]
    if len(active) < 1:
        return
    now = time.monotonic()
    hottest = max(active, key=lambda w: w["last_activity"])
    for w in watchers:
        if w is hottest or not w["started_mono"]:
            continue
        quiet = now - w["last_activity"]
        if w["had_activity"] and quiet >= SWITCH_QUIET_SECS:
            maybe_seal(w, bucket_secs, install_id, server, user, token, force=True, reason="project-switch")

def main():
    global _STOP
    ap = argparse.ArgumentParser(description="aitime layer-1 daemon (multi-project)")
    ap.add_argument("--repo", action="append", default=[], help="project root to watch (repeatable)")
    ap.add_argument("--project", default=None, help="project id when a single --repo is given")
    ap.add_argument("--project-map", action="append", default=[],
                    help="map path=project_id (repeatable)")
    ap.add_argument("--server", default=None,
                    help="API base URL; omit to use settings; pass empty string for offline")
    ap.add_argument("--user", default="")
    ap.add_argument("--token", default=None,
                    help="API token; omit to use settings; pass empty to ignore saved token")
    ap.add_argument("--bucket-secs", type=int, default=None,
                    help="override AITIME_BUCKET_SECS / gui bucket_mins (default 300)")
    ap.add_argument("--once", action="store_true", help="seal one bucket per active project and exit")
    ap.add_argument("--flush-only", action="store_true", help="retry pending ledger upload and exit")
    ap.add_argument("--debug", action="store_true",
                    help="allowlisted diagnostic logs (same as STRUGGLETIME_DEBUG=1); never paths/prompts")
    a = ap.parse_args()
    if a.debug:
        os.environ["STRUGGLETIME_DEBUG"] = "1"
        os.environ.setdefault("AITIME_DEBUG", "1")

    project_maps = {}
    for raw in a.project_map or []:
        if "=" in raw:
            path, pid = raw.split("=", 1)
            project_maps[path.strip()] = pid.strip()
            try:
                project_maps[str(Path(path).expanduser().resolve())] = pid.strip()
            except Exception:
                pass

    primary_repo = a.repo[0] if a.repo else ""
    rt = dcfg.resolve_runtime(
        cli_server=a.server,  # None → settings; "" → offline
        cli_token=a.token,
        cli_user=a.user,
        cli_repo=primary_repo, cli_project=a.project or "",
    )
    # Merge all CLI repos into runtime projects
    for extra in a.repo[1:]:
        rt["projects"] = dcfg._normalize_projects(
            list(rt.get("projects") or []) + [{"id": "", "path": extra}],
            rt.get("repo") or "", rt.get("project") or "",
        )

    watchers = resolve_watch_list(a.repo, project_maps, a.project, rt)
    if not watchers:
        print("no project roots to watch (--repo or settings projects[])", file=sys.stderr)
        return 1

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
    # Shared drain cursors (per-machine); also mirrored into each ledger
    shared_state = {"exec_offset": 0, "agent_offset": 0}
    for w in watchers:
        shared_state["exec_offset"] = max(shared_state["exec_offset"], int(w["state"].get("exec_offset") or 0))
        shared_state["agent_offset"] = max(shared_state["agent_offset"], int(w["state"].get("agent_offset") or 0))

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
    for w in watchers:
        if server and (w["state"].get("prev_hash") == GENESIS or not w["state"].get("pending")):
            adopt_chain_head(w["state"], server, token, w["project"], w["ledger"])
        flush_pending(w["state"], server, user, token, w["ledger"], project=w["project"])
    if a.flush_only:
        pending = sum(len(w["state"].get("pending") or []) for w in watchers)
        return 0 if pending == 0 else 1

    install_id = env("STRUGGLETIME_INSTALL", "AITIME_INSTALL", default="local")
    roots = ", ".join(f"{w['project']}←{w['repo'].name}" for w in watchers)
    print(
        f"watching {len(watchers)} project(s) [{roots}] bucket={bucket_secs}s "
        f"server={server or 'offline'} os={host_os()['id']} "
        f"agents={agent_log.name}+{exec_log.name}"
        f"{' PAUSED' if was_paused else ''}"
        f"{' DEBUG' if debugutil.debug_enabled() else ''}",
        flush=True,
    )
    debugutil.debug_log("boot", n=len(watchers), kind="daemon")

    # Seed a window for repos that already have dirty work, or when --once needs one seal
    now_wall, now_mono = int(time.time()), time.monotonic()
    for w in watchers:
        dirty = bool(git(w["repo"], "status", "--porcelain").strip())
        if dirty or a.once:
            w["started_wall"] = now_wall
            w["started_mono"] = now_mono
            w["last_activity"] = now_mono
            if dirty:
                note_activity(w, now_wall, now_mono)

    while not _STOP:
        time.sleep(1)

        if os.environ.pop("_ST_FLUSH_NOW", None):
            for w in watchers:
                flush_pending(w["state"], server, user, token, w["ledger"], project=w["project"])

        paused_now = dcfg.is_paused()
        if paused_now != was_paused:
            print(f"{'paused — still watching, not sealing' if paused_now else 'resumed'}", flush=True)
            was_paused = paused_now
            if not paused_now:
                for w in watchers:
                    reset_window(w)
        if paused_now:
            continue

        try:
            drain_shared_logs(watchers, agent_log, exec_log, shared_state)
            for w in watchers:
                poll_repo_files(w, bucket_secs)
            handle_project_switch(watchers, bucket_secs, install_id, server, user, token)
        except Exception as e:
            print(f"poll error (continuing): {e}", flush=True)
            continue

        sealed_any = False
        for w in watchers:
            if maybe_seal(w, bucket_secs, install_id, server, user, token):
                sealed_any = True
        if a.once and sealed_any:
            for w in watchers:
                if w["started_mono"] and w["had_activity"]:
                    maybe_seal(w, bucket_secs, install_id, server, user, token, force=True, reason="once")
            return 0
        if a.once and all(not w["started_mono"] for w in watchers):
            # Nothing left to seal
            return 0

    print("shutdown: flushing pending…", flush=True)
    for w in watchers:
        if w["started_mono"] and w["had_activity"]:
            maybe_seal(w, bucket_secs, install_id, server, user, token, force=True, reason="shutdown")
        flush_pending(w["state"], server, user, token, w["ledger"], project=w["project"])
        save_ledger(w["ledger"], w["state"])
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
