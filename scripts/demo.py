#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start a server on :18766 seeded with two demo builders and a pending submission."""
import json, os, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
import verify

PORT = os.environ.get("PORT", "18766")
BASE = f"http://127.0.0.1:{PORT}"

def req(method, path, body=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r) as x: return json.loads(x.read())

def chain(pid, specs):
    prev, out = verify.GENESIS, []
    for i, extra in enumerate(specs):
        counts = {"human_edits": 0, "ai_writes": 0, "prompts": 0, "executions": 0, "exec_failures": 0,
                  "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0, **extra}
        b = {"v": 1, "bucket_id": str(uuid.uuid4()), "install_id": "demo", "project_id": pid, "repo_remote_hash": None,
             "bucket_start_wall": 1760000000 + i * 300, "bucket_secs": 300, "mono_delta_ms": 300000, "counts": counts,
             "git": {"head_commit": f"a{i}b2c3", "lines_added": 25, "lines_deleted": 6, "dirty_files": 2},
             "files": [{"file_id": "0123456789abcdef", "added": 15, "deleted": 4, "bursts": 5}],
             "formula_version": "v0.1.0-alpha", "prev_hash": prev}
        b["hash"] = verify.bucket_hash(b); prev = b["hash"]; out.append(b)
    return out

def main():
    env = {**os.environ, "PORT": PORT, "SERVER_DB": os.environ.get("SERVER_DB", "/tmp/struggletime-demo.db")}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server/server.py")], env=env)
    try:
        for _ in range(50):
            try: req("GET", "/health"); break
            except Exception: time.sleep(0.1)
        req("POST", "/v1/users", {"handle": "rowan"}); req("POST", "/v1/users", {"handle": "ada"})
        req("POST", "/v1/projects", {"id": "cs2-browser", "owner_handle": "rowan", "name": "CS2 Browser Port", "repo_url": "https://github.com/example/cs2-browser"})
        req("POST", "/v1/projects", {"id": "portfolio-ai", "owner_handle": "ada", "name": "Portfolio AI", "repo_url": "https://github.com/example/portfolio-ai"})
        closed = chain("cs2-browser", [{"prompts": 2, "human_edits": 8, "executions": 2, "exec_failures": 1},
                                       {"human_edits": 10, "executions": 2, "exec_failures": 1},
                                       {"human_edits": 6, "executions": 1, "commits": 1},
                                       {"prompts": 1, "human_edits": 4, "executions": 1},
                                       {"human_edits": 9, "executions": 3, "exec_failures": 2},
                                       {"human_edits": 5, "commits": 1}])
        req("POST", "/v1/buckets/batch", {"user_id": "rowan", "buckets": closed})
        req("POST", "/v1/buckets/batch", {"user_id": "ada", "buckets": chain("portfolio-ai", [{"ai_writes": 15, "commits": 1}, {"ai_writes": 12}])})
        ids = [b["bucket_id"] for b in closed]
        req("POST", "/v1/submissions", {"user_id": "rowan", "project_id": "cs2-browser", "from_bucket": min(ids),
            "to_bucket": max(ids), "ai_declaration": "claude assisted; i reviewed, debugged and tested every change"})
        print(f"demo live at {BASE} — ctrl-c to stop")
        proc.wait()
    except KeyboardInterrupt: proc.terminate()

if __name__ == "__main__":
    main()
