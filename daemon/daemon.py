#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layer-1 daemon: fs watcher + exec log -> chained 5-min buckets -> server.

Cross-platform (polling, no native deps), low-spec friendly.
Raw file names, commands, prompts and code never leave this process:
only counts, categories, exit codes and git totals are bucketed.
"""
import argparse, json, os, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from verify import bucket_hash, anonymized_file_id, GENESIS

IGNORE = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}

def git(repo, *args):
    try: return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception: return ""

def git_anchor(repo, salt=None, bursts=None):
    added = deleted = 0; files = {}
    for line in git(repo, "diff", "--numstat").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            a = int(p[0]) if p[0].isdigit() else 0; d = int(p[1]) if p[1].isdigit() else 0
            added += a; deleted += d
            if salt and len(p) >= 3:
                fid = anonymized_file_id(p[2], salt)
                files[fid] = {"file_id": fid, "added": a, "deleted": d, "bursts": (bursts or {}).get(p[2], 0)}
    for rel, n in (bursts or {}).items():
        if salt and anonymized_file_id(rel, salt) not in files:
            fid = anonymized_file_id(rel, salt); files[fid] = {"file_id": fid, "added": 0, "deleted": 0, "bursts": n}
    dirty = len([l for l in git(repo, "status", "--porcelain").splitlines() if l])
    anchor = {"head_commit": git(repo, "rev-parse", "HEAD") or None, "lines_added": added, "lines_deleted": deleted, "dirty_files": dirty}
    return anchor, list(files.values())[:50]

def snapshot(repo):
    out = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in IGNORE]
        for f in files:
            p = Path(root) / f
            try: out[str(p.relative_to(repo))] = p.stat().st_mtime_ns
            except Exception: pass
    return out

def classify_exec(line):
    return json.loads(line)

def empty():
    return {"human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0,
            "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--project", default=None)
    ap.add_argument("--server", default=os.environ.get("AITIME_SERVER", ""))
    ap.add_argument("--user", default=os.environ.get("AITIME_USER", ""))
    ap.add_argument("--bucket-secs", type=int, default=int(os.environ.get("AITIME_BUCKET_SECS", "300")))
    ap.add_argument("--once", action="store_true", help="flush one bucket and exit (tests/demo)")
    a = ap.parse_args(); repo = Path(a.repo).resolve()
    project = a.project or repo.name
    exec_log = Path(os.environ.get("AITIME_EXEC_LOG", Path.home() / ".aitime" / "exec.jsonl"))
    ledger = repo / ".aitime-ledger.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else {"prev_hash": GENESIS, "pending": [], "exec_offset": 0}
    if "salt" not in state: state["salt"] = uuid.uuid4().hex
    gi = repo / ".gitignore"
    if (repo / ".git").exists() and (not gi.exists() or ".aitime-ledger.json" not in gi.read_text(errors="ignore")):
        open(gi, "a").write("\n.aitime-ledger.json\n")
    bursts, counts, prev_snap, last_activity = {}, empty(), snapshot(repo), time.monotonic()
    started_wall, started_mono = int(time.time()), time.monotonic()
    print(f"watching {repo} project={project} bucket={a.bucket_secs}s", flush=True)
    while True:
        time.sleep(1)
        snap = snapshot(repo)
        changed = [k for k in snap if k not in prev_snap or snap[k] != prev_snap.get(k)]
        if changed:
            last_activity = time.monotonic()
            for rel in changed: bursts[rel] = bursts.get(rel, 0) + 1
            if len(changed) >= 5: counts["ai_writes"] += len(changed)
            else: counts["human_edits"] += len(changed)
        prev_snap = snap
        if exec_log.exists():
            lines = exec_log.read_text().splitlines()
            for line in lines[state["exec_offset"]:]:
                try: e = classify_exec(line)
                except Exception: continue
                counts["executions"] += 1
                if e.get("exit_code"): counts["exec_failures"] += 1
                if e.get("category") == "ai_cli": counts["prompts"] += 1
                if e.get("category") == "git" and e.get("verb") == "commit": counts["commits"] += 1
            state["exec_offset"] = len(lines)
        if time.monotonic() - started_mono < a.bucket_secs: continue
        counts["idle_secs"] = int(max(0, a.bucket_secs - (last_activity - started_mono))) if time.monotonic() - last_activity > 60 else 0
        anchor, files = git_anchor(repo, state["salt"], bursts)
        bucket = {"v": 1, "bucket_id": str(uuid.uuid4()), "install_id": os.environ.get("AITIME_INSTALL", "local"),
                  "project_id": project, "repo_remote_hash": None, "bucket_start_wall": started_wall,
                  "bucket_secs": a.bucket_secs, "mono_delta_ms": int((time.monotonic() - started_mono) * 1000),
                  "counts": counts, "git": anchor, "files": files, "formula_version": "v0.1.0-alpha", "prev_hash": state["prev_hash"]}
        bursts = {}
        bucket["hash"] = bucket_hash(bucket); state["prev_hash"] = bucket["hash"]; state["pending"].append(bucket)
        if a.server:
            req = urllib.request.Request(a.server.rstrip("/") + "/v1/buckets/batch", method="POST",
                data=json.dumps({"user_id": a.user, "buckets": state["pending"]}).encode(), headers={"Content-Type": "application/json"})
            print(urllib.request.urlopen(req).read().decode(), flush=True); state["pending"] = []
        ledger.write_text(json.dumps(state)); print(f"bucket {bucket['bucket_id'][:8]} {counts}", flush=True)
        if a.once: return
        counts, started_wall, started_mono = empty(), int(time.time()), time.monotonic()

if __name__ == "__main__":
    main()
