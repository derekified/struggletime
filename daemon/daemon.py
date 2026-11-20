#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layer-1 aitime daemon: polling watcher + exec log -> chained 5-min buckets.

Stdlib only, cross-platform (no native file watchers). Metadata only —
raw paths never leave this process (anonymized file ids in the ledger salt).
"""
import argparse, json, os, signal, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
from verify import bucket_hash, anonymized_file_id, GENESIS
import config as dcfg

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

def env(*keys, default=""):
    return dcfg.env(*keys, default=default)

def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def git_anchor(repo, salt=None, bursts=None):
    added = deleted = 0
    files = {}
    for line in git(repo, "diff", "--numstat").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            a = int(p[0]) if p[0].isdigit() else 0
            d = int(p[1]) if p[1].isdigit() else 0
            added += a
            deleted += d
            if salt and len(p) >= 3:
                fid = anonymized_file_id(p[2], salt)
                files[fid] = {"file_id": fid, "added": a, "deleted": d, "bursts": (bursts or {}).get(p[2], 0)}
    for rel, n in (bursts or {}).items():
        if salt and anonymized_file_id(rel, salt) not in files:
            fid = anonymized_file_id(rel, salt)
            files[fid] = {"file_id": fid, "added": 0, "deleted": 0, "bursts": n}
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

def classify_exec(line):
    """Parse shell-hook JSONL. Never keep args — only category/verb/exit_code."""
    e = json.loads(line)
    if not isinstance(e, dict):
        raise ValueError("exec line must be object")
    cat = e.get("category") or classify_name(e.get("name") or e.get("cmd"))
    if cat not in ("git", "test_runner", "compiler", "ai_cli", "other", "editor"):
        cat = "other"
    verb = e.get("verb") or ""
    # strip anything that looks like an argument list
    if " " in verb or "/" in verb or len(verb) > 32:
        verb = ""
    return {
        "category": cat,
        "verb": verb,
        "exit_code": int(e.get("exit_code") or 0),
        "ts": e.get("ts"),
    }

def empty():
    return {
        "human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0,
        "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
    }

def classify_burst(changed, prev_snap, snap):
    """1–2 files = human; 5+ files = agent/formatter (huge diffs handled at seal)."""
    return "ai" if len(changed) >= 5 else "human"

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

def save_ledger(ledger, state):
    ledger.write_text(json.dumps(state, indent=2))

def flush_pending(state, server, user, token, ledger):
    if not server or not state.get("pending"):
        return False
    ok, msg = post_buckets(server, user, token, state["pending"])
    if ok:
        print(f"synced {len(state['pending'])} bucket(s): {msg}", flush=True)
        state["pending"] = []
        save_ledger(ledger, state)
        return True
    print(f"sync deferred ({len(state['pending'])} pending): {msg}", flush=True)
    return False

def _on_signal(signum, _frame):
    global _STOP
    if signum in (signal.SIGTERM, signal.SIGINT):
        print(f"signal {signum}: shutting down…", flush=True)
        _STOP = True
    elif hasattr(signal, "SIGUSR2") and signum == signal.SIGUSR2:
        os.environ["_ST_FLUSH_NOW"] = "1"

def seal_bucket(state, repo, project, bucket_secs, started_wall, started_mono, counts, bursts, install_id):
    anchor, files = git_anchor(repo, state["salt"], bursts)
    # huge instant diff: many lines added+deleted in one seal window → agentic write signal
    if (anchor["lines_added"] + anchor["lines_deleted"]) >= 200 and counts["human_edits"] > 0 and counts["ai_writes"] == 0:
        # reclassify leftover human_edits into ai_writes for dump-shaped diffs
        dump = counts["human_edits"]
        counts = dict(counts)
        counts["ai_writes"] += dump
        counts["human_edits"] = 0
    bucket = {
        "v": 1, "bucket_id": str(uuid.uuid4()), "install_id": install_id,
        "project_id": project, "repo_remote_hash": None, "bucket_start_wall": started_wall,
        "bucket_secs": bucket_secs, "mono_delta_ms": int((time.monotonic() - started_mono) * 1000),
        "counts": counts, "git": anchor, "files": files, "formula_version": "v0.1.0-alpha",
        "prev_hash": state["prev_hash"],
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
        print("repo required (--repo or ~/.aitime/gui.json)", file=sys.stderr)
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

    exec_log = Path(env("STRUGGLETIME_EXEC_LOG", "AITIME_EXEC_LOG", default=str(dcfg.HOME / "exec.jsonl")))
    ledger = repo / ".aitime-ledger.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else {"prev_hash": GENESIS, "pending": [], "exec_offset": 0}
    state.setdefault("pending", [])
    state.setdefault("exec_offset", 0)
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
    flush_pending(state, server, user, token, ledger)
    if a.flush_only:
        return 0 if not state.get("pending") else 1

    bursts, counts, prev_snap, last_activity = {}, empty(), snapshot(repo), time.monotonic()
    started_wall, started_mono = int(time.time()), time.monotonic()
    install_id = env("STRUGGLETIME_INSTALL", "AITIME_INSTALL", default="local")
    print(
        f"watching {repo} project={project} bucket={bucket_secs}s server={server or 'offline'}"
        f"{' PAUSED' if was_paused else ''}",
        flush=True,
    )
    while not _STOP:
        time.sleep(1)

        if os.environ.pop("_ST_FLUSH_NOW", None):
            flush_pending(state, server, user, token, ledger)

        paused_now = dcfg.is_paused()
        if paused_now != was_paused:
            print(f"{'paused — still watching, not sealing' if paused_now else 'resumed'}", flush=True)
            was_paused = paused_now
            if not paused_now:
                started_wall, started_mono = int(time.time()), time.monotonic()
                counts, bursts = empty(), {}
        if paused_now:
            continue

        snap = snapshot(repo)
        changed = [k for k in snap if k not in prev_snap or snap[k] != prev_snap.get(k)]
        if changed:
            last_activity = time.monotonic()
            for rel in changed:
                bursts[rel] = bursts.get(rel, 0) + 1
            kind = classify_burst(changed, prev_snap, snap)
            if kind == "ai":
                counts["ai_writes"] += len(changed)
            else:
                counts["human_edits"] += len(changed)
        prev_snap = snap

        if exec_log.exists():
            try:
                lines = exec_log.read_text().splitlines()
            except Exception:
                lines = []
            for line in lines[state["exec_offset"]:]:
                try:
                    e = classify_exec(line)
                except Exception:
                    continue
                counts["executions"] += 1
                if e.get("exit_code"):
                    counts["exec_failures"] += 1
                if e.get("category") == "ai_cli":
                    counts["prompts"] += 1
                if e.get("category") == "git" and e.get("verb") == "commit":
                    counts["commits"] += 1
            state["exec_offset"] = len(lines)

        if time.monotonic() - started_mono < bucket_secs:
            continue
        counts["idle_secs"] = (
            int(max(0, bucket_secs - (last_activity - started_mono)))
            if time.monotonic() - last_activity > 60 else 0
        )
        bucket = seal_bucket(state, repo, project, bucket_secs, started_wall, started_mono, counts, bursts, install_id)
        bursts = {}
        save_ledger(ledger, state)
        print(f"bucket {bucket['bucket_id'][:8]} sealed {bucket['counts']}", flush=True)
        flush_pending(state, server, user, token, ledger)
        if a.once:
            return 0
        counts, started_wall, started_mono = empty(), int(time.time()), time.monotonic()

    print("shutdown: flushing pending…", flush=True)
    flush_pending(state, server, user, token, ledger)
    save_ledger(ledger, state)
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
