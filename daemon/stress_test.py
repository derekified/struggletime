#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Internal stress / acceptance tool: N weighted projects through projects + buckets APIs.

  # ephemeral local server (no Slack), 1000 projects:
  python3 daemon/stress_test.py --count 1000

  # quick smoke (5 projects) against a local ephemeral server:
  python3 daemon/stress_test.py --smoke

  # against a running server (session bearer required):
  python3 daemon/stress_test.py --server http://127.0.0.1:18765 --token "$TOKEN" --count 1000

  # remove synthetic projects from a prior run:
  python3 daemon/stress_test.py --server … --token … --cleanup --run-id abcdef

Project ids are tagged `sts{run_id}{nnnn}` so they are queryable and cleanable.
Does not touch daemon.py Layer-1 tracking or ~/.aitime/gui.json.
"""
from __future__ import annotations

import argparse, hashlib, json, math, os, pathlib, secrets, sqlite3, statistics, subprocess, sys, tempfile, time, uuid
from urllib import error, request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))
from verify import GENESIS, bucket_hash  # noqa: E402

# sts + 6 hex run + 4 digits = 13 chars (fits [a-z0-9_-]{1,40})
ID_PREFIX = "sts"


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def api(server, method, path, token=None, body=None, timeout=30):
    url = server.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, method=method, data=data, headers=headers)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as x:
            raw = x.read() or b"{}"
            ms = (time.perf_counter() - t0) * 1000
            return x.status, json.loads(raw), ms
    except error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        try:
            return e.code, json.loads(e.read() or b"{}"), ms
        except Exception:
            return e.code, {"error": str(e)}, ms
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return 0, {"error": str(e)}, ms


def project_id(run_id, i):
    return f"{ID_PREFIX}{run_id}{i:04d}"


def human_counts():
    return {
        "human_edits": 6, "ai_writes": 0, "prompts": 1, "executions": 2,
        "exec_failures": 1, "commits": 1, "dwell_diff_secs": 40, "idle_secs": 0,
    }


def agent_counts():
    return {
        "human_edits": 0, "ai_writes": 12, "prompts": 0, "executions": 0,
        "exec_failures": 0, "commits": 0, "dwell_diff_secs": 5, "idle_secs": 0,
    }


def make_bucket(project, prev, start, secs, counts, install="aitime-stress"):
    b = {
        "v": 1,
        "bucket_id": str(uuid.uuid4()),
        "install_id": install,
        "project_id": project,
        "repo_remote_hash": None,
        "bucket_start_wall": start,
        "bucket_secs": secs,
        "mono_delta_ms": secs * 1000,
        "counts": counts,
        "git": {"head_commit": "cafebabe", "lines_added": 18, "lines_deleted": 3, "dirty_files": 2},
        "files": [{
            "file_id": hashlib.sha256(f"{project}:main".encode()).hexdigest()[:16],
            "added": 10, "deleted": 2, "bursts": 1,
            "op": "modified", "editor": "cursor", "source": "layer1",
        }],
        "editors": {"cursor": 1},
        "formula_version": "v0.1.0-alpha",
        "prev_hash": prev,
    }
    b["hash"] = bucket_hash(b)
    return b


def seed_local_user(db_path, handle="stress"):
    token = secrets.token_urlsafe(24)
    th = hashlib.sha256(token.encode()).hexdigest()
    now = int(time.time())
    c = sqlite3.connect(db_path)
    c.execute(
        "INSERT INTO user(handle, token_hash, is_public, created_at) VALUES (?,?,?,?)",
        (handle, "", 1, now),
    )
    c.execute(
        "INSERT INTO link(handle, github_login, slack_user_id, github_token, slack_avatar, slack_name) VALUES (?,?,?,?,?,?)",
        (handle, None, "Ustress", None, None, handle),
    )
    c.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?)",
        (th, handle, "Ustress", "", "", now, now + 86400),
    )
    c.commit()
    c.close()
    return token


def start_ephemeral_server(port=0):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="st-stress-"))
    db = tmp / "stress.db"
    # bind 0 → ask OS; we pick a free port ourselves for simpler env
    import socket
    if port <= 0:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {
        **os.environ,
        "PORT": str(port),
        "SERVER_DB": str(db),
        "SLACK_WEBHOOK_URL": "",
        "OAUTH_REDIRECT_BASE": f"http://127.0.0.1:{port}",
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "server.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    else:
        proc.terminate()
        raise SystemExit("ephemeral server failed to start")
    # seed after schema init (first request created tables)
    token = seed_local_user(db)
    return proc, f"http://127.0.0.1:{port}", token, tmp


def list_stress_projects(server, token, run_id=None):
    code, data, _ = api(server, "GET", "/v1/projects", token=token)
    if code != 200:
        return []
    out = []
    for p in data.get("projects") or []:
        pid = p.get("id") or ""
        if not pid.startswith(ID_PREFIX):
            continue
        if run_id and not pid.startswith(ID_PREFIX + run_id):
            continue
        if p.get("deleted"):
            continue
        out.append(p)
    return out


def cleanup_projects(server, token, run_id, latencies=None):
    projects = list_stress_projects(server, token, run_id=run_id)
    ok = fail = 0
    for p in projects:
        pid = p["id"]
        code, data, ms = api(
            server, "POST", f"/v1/projects/{pid}/delete",
            token=token, body={"mode": "project", "confirm": pid},
        )
        if latencies is not None:
            latencies.append(ms)
        if code == 200:
            ok += 1
        else:
            fail += 1
            print(f"  cleanup fail {pid}: {code} {data}", flush=True)
    return ok, fail


def run_stress(server, token, count, run_id, buckets_per=1, verify_sample=50):
    lat_create, lat_bucket, lat_verify = [], [], []
    created, create_fail = [], 0
    ingested, ingest_fail = 0, 0
    weighted_secs = 0.0
    t_wall0 = time.perf_counter()

    print(f"creating {count} projects (run_id={run_id})…", flush=True)
    for i in range(count):
        pid = project_id(run_id, i)
        code, data, ms = api(
            server, "POST", "/v1/projects",
            token=token,
            body={
                "id": pid,
                "name": f"Stress {run_id} #{i}",
                "repo_url": f"https://github.com/example/{pid}",
            },
        )
        lat_create.append(ms)
        if code in (200, 201):
            created.append(pid)
        elif code == 409:
            created.append(pid)  # already exists from prior partial run
        else:
            create_fail += 1
            if create_fail <= 5:
                print(f"  create fail {pid}: {code} {data}", flush=True)
        if (i + 1) % 100 == 0 or i + 1 == count:
            print(f"  created {i + 1}/{count} (fail={create_fail})", flush=True)

    print(f"ingesting buckets across {len(created)} projects…", flush=True)
    now = int(time.time())
    for n, pid in enumerate(created):
        prev = GENESIS
        batch = []
        for b_i in range(buckets_per):
            # alternate human struggle vs agent dump for weight variety
            counts = human_counts() if (n + b_i) % 3 else agent_counts()
            start = now - (len(created) - n) * 60 - b_i * 300
            batch.append(make_bucket(pid, prev, start, 300, counts))
            prev = batch[-1]["hash"]
        code, data, ms = api(
            server, "POST", "/v1/buckets/batch",
            token=token,
            body={"buckets": batch, "user_id": "stress"},
        )
        lat_bucket.append(ms)
        if code in (200, 201):
            ingested += 1
            weighted_secs += float(data.get("weighted_secs_added") or 0)
        else:
            ingest_fail += 1
            if ingest_fail <= 5:
                print(f"  ingest fail {pid}: {code} {data}", flush=True)
        if (n + 1) % 100 == 0 or n + 1 == len(created):
            print(f"  ingested {n + 1}/{len(created)} (fail={ingest_fail})", flush=True)

    # verify: list + spot-check stats
    print("verifying…", flush=True)
    code, listing, ms = api(server, "GET", "/v1/projects", token=token)
    lat_verify.append(ms)
    listed = []
    if code == 200:
        listed = [
            p for p in (listing.get("projects") or [])
            if (p.get("id") or "").startswith(ID_PREFIX + run_id) and not p.get("deleted")
        ]
    verified_stats = 0
    sample = created[: max(1, min(verify_sample, len(created)))]
    for pid in sample:
        code, st, ms = api(server, "GET", f"/v1/projects/{pid}/stats", token=token)
        lat_verify.append(ms)
        if code == 200 and (st.get("buckets") or 0) >= 1:
            verified_stats += 1

    wall = time.perf_counter() - t_wall0

    def lat_summary(xs):
        xs = sorted(xs)
        return {
            "n": len(xs),
            "p50_ms": round(percentile(xs, 50), 2),
            "p95_ms": round(percentile(xs, 95), 2),
            "max_ms": round(max(xs), 2) if xs else 0.0,
            "mean_ms": round(statistics.fmean(xs), 2) if xs else 0.0,
        }

    report = {
        "run_id": run_id,
        "target_count": count,
        "projects_created": len(created),
        "create_failures": create_fail,
        "projects_listed": len(listed),
        "projects_with_buckets_sampled": verified_stats,
        "sample_size": len(sample),
        "projects_ingested": ingested,
        "ingest_failures": ingest_fail,
        "weighted_secs_added": round(weighted_secs, 2),
        "weighted_hours_added": round(weighted_secs / 3600, 4),
        "wall_secs": round(wall, 2),
        "latency": {
            "create": lat_summary(lat_create),
            "bucket": lat_summary(lat_bucket),
            "verify": lat_summary(lat_verify),
        },
        "ok": (
            len(created) >= count
            and create_fail == 0
            and ingest_fail == 0
            and len(listed) >= count
            and verified_stats == len(sample)
        ),
    }
    return report


def print_report(report):
    print("\n=== stress report ===", flush=True)
    print(f"run_id:              {report['run_id']}", flush=True)
    print(f"projects created:    {report['projects_created']}/{report['target_count']} (fail={report['create_failures']})", flush=True)
    print(f"projects listed:     {report['projects_listed']}", flush=True)
    print(f"ingested:            {report['projects_ingested']} (fail={report['ingest_failures']})", flush=True)
    print(f"sample stats ok:     {report['projects_with_buckets_sampled']}/{report['sample_size']}", flush=True)
    print(f"weighted hours:      {report['weighted_hours_added']} ({report['weighted_secs_added']} secs)", flush=True)
    print(f"wall time:           {report['wall_secs']}s", flush=True)
    for name, lat in report["latency"].items():
        print(
            f"latency {name:7s}  n={lat['n']}  p50={lat['p50_ms']}ms  p95={lat['p95_ms']}ms  max={lat['max_ms']}ms  mean={lat['mean_ms']}ms",
            flush=True,
        )
    print(f"PASS" if report["ok"] else "FAIL", flush=True)


def main():
    ap = argparse.ArgumentParser(prog="aitime-stress", description="Stress / acceptance: N weighted projects")
    ap.add_argument("--count", type=int, default=1000, help="number of projects (default 1000)")
    ap.add_argument("--smoke", action="store_true", help="shortcut: --count 5 on ephemeral server")
    ap.add_argument("--server", default="", help="existing server base URL (else ephemeral local)")
    ap.add_argument("--token", default="", help="session bearer (required with --server)")
    ap.add_argument("--run-id", default="", help="6-char hex tag (default: random)")
    ap.add_argument("--buckets-per", type=int, default=1, help="sealed buckets per project")
    ap.add_argument("--verify-sample", type=int, default=50, help="how many projects to stats-check")
    ap.add_argument("--cleanup", action="store_true", help="soft-delete sts{run_id}* projects and exit")
    ap.add_argument("--cleanup-after", action="store_true", help="cleanup after a successful run")
    ap.add_argument("--json", action="store_true", help="print machine-readable report JSON")
    a = ap.parse_args()

    if a.smoke:
        a.count = 5
        a.verify_sample = 5

    run_id = (a.run_id or uuid.uuid4().hex[:6]).lower()
    if not (len(run_id) == 6 and all(c in "0123456789abcdef" for c in run_id)):
        print("run-id must be 6 hex chars", file=sys.stderr)
        return 2

    proc = None
    tmp = None
    server = (a.server or "").rstrip("/")
    token = a.token or ""

    try:
        if not server:
            print("starting ephemeral server…", flush=True)
            proc, server, token, tmp = start_ephemeral_server()
            print(f"server {server}", flush=True)
        elif not token:
            print("--token required with --server", file=sys.stderr)
            return 2

        if a.cleanup:
            ok, fail = cleanup_projects(server, token, run_id)
            print(f"cleanup: deleted={ok} fail={fail} run_id={run_id}", flush=True)
            return 0 if fail == 0 else 1

        report = run_stress(
            server, token, a.count, run_id,
            buckets_per=max(1, a.buckets_per),
            verify_sample=max(1, a.verify_sample),
        )
        print_report(report)
        if a.json:
            print(json.dumps(report, indent=2), flush=True)

        if a.cleanup_after:
            ok, fail = cleanup_projects(server, token, run_id)
            print(f"cleanup-after: deleted={ok} fail={fail}", flush=True)

        return 0 if report["ok"] else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
