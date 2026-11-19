#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib, hmac, json, os, re, secrets, sqlite3, sys, threading, time, urllib.request, uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from verify import bucket_hash, weight, weight_sequence, flags, friction, friction_timeline, validate_allowlist, GENESIS

REASONS = {"UNATTENDED_AGENT_DUMP","IDLE_INFLATION","NO_TEST_ITERATION","BOILERPLATE_HEAVY","CHAIN_BROKEN","GIT_MISMATCH","NONE"}
DB = os.environ.get("SERVER_DB", "server.db")
STATIC = Path(__file__).parent / "static"

_LOCK = threading.RLock(); _CONN = None
def db():
    global _CONN
    if _CONN is not None: return _CONN
    c = sqlite3.connect(DB, timeout=10, check_same_thread=False); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS bucket(
      bucket_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, bucket_start_wall INT,
      bucket_secs INT, state TEXT, weight REAL, weighted_secs REAL, hash TEXT, prev_hash TEXT, payload TEXT);
    CREATE TABLE IF NOT EXISTS chain_head(project_id TEXT PRIMARY KEY, last_hash TEXT);
    CREATE TABLE IF NOT EXISTS submission(
      id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, claimed_weighted_secs REAL,
      ai_declaration TEXT, status TEXT, flags TEXT);
    CREATE TABLE IF NOT EXISTS review(
      id TEXT PRIMARY KEY, submission_id TEXT, reviewer_id TEXT,
      approved_weighted_secs REAL, reason_code TEXT);
    CREATE TABLE IF NOT EXISTS user(
      handle TEXT PRIMARY KEY, token_hash TEXT, is_public INT NOT NULL DEFAULT 1, created_at INT);
    CREATE TABLE IF NOT EXISTS silent_streak(project_id TEXT PRIMARY KEY, silent_secs INT NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS link(handle TEXT PRIMARY KEY, github_login TEXT, slack_user_id TEXT);
    CREATE TABLE IF NOT EXISTS integration_event(id TEXT PRIMARY KEY, kind TEXT, text TEXT, delivered INT NOT NULL DEFAULT 0, created_at INT);
    CREATE TABLE IF NOT EXISTS github_event(id TEXT PRIMARY KEY, project_id TEXT, repo TEXT, commits INT, head TEXT);
    CREATE TABLE IF NOT EXISTS project(
      id TEXT PRIMARY KEY, owner TEXT, name TEXT, repo_url TEXT, head_from TEXT, head_to TEXT);
    """)
    _CONN = c; return c

def notify(c, text):
    eid = str(uuid.uuid4()); delivered = 0
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        try:
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
            delivered = 1 if urllib.request.urlopen(req, timeout=3).status < 300 else 0
        except Exception: delivered = 0
    c.execute("INSERT INTO integration_event VALUES (?,?,?,?,?)", (eid, "slack", text, delivered, int(time.time())))
    return delivered

def stats_for(c, project=None, user=None):
    q, args = "SELECT * FROM bucket WHERE 1=1", []
    if project: q += " AND project_id=?"; args.append(project)
    if user: q += " AND user_id=?"; args.append(user)
    rows = c.execute(q, args).fetchall()
    weighted = sum(r["weighted_secs"] for r in rows)
    by_state, days = {}, set()
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        days.add(date.fromtimestamp(r["bucket_start_wall"]).isoformat())
    streak, d = 0, date.today()
    while d.isoformat() in days: streak += 1; d -= timedelta(days=1)
    buckets = [json.loads(r["payload"]) for r in rows]
    return {"buckets": len(rows), "raw_secs": sum(r["bucket_secs"] for r in rows),
            "weighted_secs": weighted, "weighted_hours": round(weighted / 3600, 4),
            "by_state": by_state, "active_days": len(days), "current_streak_days": streak,
            "flags": flags(buckets, weighted / 3600) if buckets else []}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def raw(self):
        if not hasattr(self, "_raw"): self._raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._raw
    def body(self):
        return json.loads(self.raw() or b"{}")
    def auth_handle(self, c):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "): return None
        th = hashlib.sha256(auth[7:].encode()).hexdigest()
        r = c.execute("SELECT handle FROM user WHERE token_hash=?", (th,)).fetchone()
        return r["handle"] if r else None

    def do_GET(self):
        with _LOCK:
            try: return self._get()
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception as e: return self.send_json(500, {"error": f"internal: {type(e).__name__}"})
    def _get(self):
        parsed = urlparse(self.path); p = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []
        c = db()
        if p == [] or p == ["index.html"]:
            f = STATIC / "index.html"
            if not f.exists(): return self.send_json(404, {"error": "no dashboard"})
            body = f.read_bytes(); self.send_response(200)
            self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if p == ["health"]: return self.send_json(200, {"ok": True})
        if p == ["v1", "users"]:
            out = []
            for u in c.execute("SELECT handle,is_public FROM user WHERE is_public=1"):
                lk = c.execute("SELECT github_login FROM link WHERE handle=?", (u["handle"],)).fetchone()
                out.append({"handle": u["handle"], "github_login": lk["github_login"] if lk else None, **stats_for(c, user=u["handle"])})
            return self.send_json(200, {"users": sorted(out, key=lambda x: -x["weighted_hours"])})
        if len(p) == 3 and p[:2] == ["v1", "users"]:
            u = c.execute("SELECT handle,is_public FROM user WHERE handle=?", (p[2],)).fetchone()
            if not u: return self.send_json(404, {"error": "user not found"})
            if not u["is_public"] and self.auth_handle(c) != p[2]:
                return self.send_json(403, {"error": "profile is private"})
            projs = [dict(r) for r in c.execute("SELECT * FROM project WHERE owner=?", (p[2],))]
            link = c.execute("SELECT github_login, slack_user_id FROM link WHERE handle=?", (p[2],)).fetchone()
            return self.send_json(200, {"handle": p[2], "is_public": bool(u["is_public"]),
                                        "github_login": link["github_login"] if link else None,
                                        "slack_user_id": link["slack_user_id"] if link else None,
                                        "projects": projs, **stats_for(c, user=p[2])})
        if p == ["v1", "projects"]:
            out = []
            for r in c.execute("SELECT * FROM project"):
                out.append({**dict(r), **stats_for(c, project=r["id"])})
            known = {x["id"] for x in out}
            for r in c.execute("SELECT DISTINCT project_id FROM bucket"):
                if r[0] not in known: out.append({"id": r[0], "owner": None, "name": r[0], "repo_url": None, **stats_for(c, project=r[0])})
            return self.send_json(200, {"projects": out})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] in ("buckets", "friction"):
            rows = c.execute("SELECT payload FROM bucket WHERE project_id=? ORDER BY bucket_start_wall", (p[2],)).fetchall()
            if not rows: return self.send_json(404, {"error": "project not found"})
            buckets = [json.loads(r["payload"]) for r in rows]
            if p[3] == "buckets":
                return self.send_json(200, {"project_id": p[2], "buckets": buckets,
                                            "evidence_bytes": len(json.dumps(buckets).encode())})
            tl = friction_timeline(buckets)
            commits = [{"at_bucket": t["bucket_id"], "head_commit": t["head_commit"]} for t in tl if t["commits"]]
            return self.send_json(200, {"project_id": p[2], "timeline": tl, "commit_anchors": commits,
                                        "evidence_bytes": len(json.dumps(buckets).encode()),
                                        "budget": "target 25-50 kb per submitted session, hard cap 50 kb/hour of metadata"})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "stats":
            if not c.execute("SELECT 1 FROM bucket WHERE project_id=? LIMIT 1", (p[2],)).fetchone():
                return self.send_json(404, {"error": "project not found"})
            return self.send_json(200, {"project_id": p[2], **stats_for(c, project=p[2])})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "stream":
            if not c.execute("SELECT 1 FROM bucket WHERE project_id=? LIMIT 1", (p[2],)).fetchone():
                return self.send_json(404, {"error": "project not found"})
            payload = f"data: {json.dumps({'project_id': p[2], **stats_for(c, project=p[2])})}\n\n".encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache"); self.end_headers(); self.wfile.write(payload)
            if "once=1" not in parsed.query:
                _LOCK.release()
                try:
                    while True: time.sleep(15); self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass
                finally: _LOCK.acquire()
            return
        if p == ["v1", "integrations", "events"]:
            rows = c.execute("SELECT id,kind,text,delivered,created_at FROM integration_event ORDER BY rowid DESC LIMIT 50").fetchall()
            return self.send_json(200, {"events": [dict(r) for r in rows]})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "github":
            r = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not r or not r["repo_url"]: return self.send_json(404, {"error": "no repo linked"})
            m = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", r["repo_url"])
            if not m: return self.send_json(200, {"linked": False, "repo_url": r["repo_url"]})
            owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
            events = [dict(x) for x in c.execute("SELECT * FROM github_event WHERE project_id=?", (p[2],))]
            out = {"linked": True, "owner": owner, "repo": repo, "url": f"https://github.com/{owner}/{repo}", "webhook_events": events}
            if "fetch=1" in parsed.query:
                try:
                    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}", headers={"Accept": "application/vnd.github+json", "User-Agent": "struggletime"})
                    if os.environ.get("GITHUB_TOKEN"): req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
                    with urllib.request.urlopen(req, timeout=5) as x: gh = json.loads(x.read())
                    out["github"] = {"stars": gh.get("stargazers_count"), "default_branch": gh.get("default_branch"), "pushed_at": gh.get("pushed_at")}
                except Exception as e: out["github_error"] = type(e).__name__
            return self.send_json(200, out)
        if p == ["v1", "overview"]:
            st = stats_for(c)
            subs = c.execute("SELECT status, COUNT(*) n FROM submission GROUP BY status").fetchall()
            return self.send_json(200, {**st, "users": c.execute("SELECT COUNT(*) FROM user").fetchone()[0],
                "projects_count": c.execute("SELECT COUNT(DISTINCT project_id) FROM bucket").fetchone()[0],
                "submissions": {r["status"]: r["n"] for r in subs},
                "pending_reviews": sum(r["n"] for r in subs if r["status"] == "pending")})
        if p == ["v1", "submissions"]:
            rows = c.execute("SELECT * FROM submission ORDER BY rowid DESC").fetchall()
            return self.send_json(200, {"submissions": [{**dict(r), "flags": json.loads(r["flags"]),
                "claimed_weighted_hours": round(r["claimed_weighted_secs"] / 3600, 4)} for r in rows]})
        if len(p) == 3 and p[:2] == ["v1", "submissions"]:
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            return self.send_json(200, {**dict(r), "flags": json.loads(r["flags"])})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        with _LOCK:
            try: return self._post()
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return self.send_json(400, {"error": f"malformed request: {type(e).__name__}"})
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception as e: return self.send_json(500, {"error": f"internal: {type(e).__name__}"})
    def _post(self):
        p = urlparse(self.path).path.strip("/").split("/"); c = db()
        if p == ["v1", "users"]:
            d = self.body(); handle = (d.get("handle") or "").strip()
            if not handle: return self.send_json(400, {"error": "handle required"})
            token = secrets.token_urlsafe(24)
            try:
                c.execute("INSERT INTO user VALUES (?,?,?,?)", (handle, hashlib.sha256(token.encode()).hexdigest(),
                          0 if d.get("is_public") is False else 1, int(time.time()))); c.commit()
            except sqlite3.IntegrityError: return self.send_json(409, {"error": "handle taken"})
            return self.send_json(201, {"handle": handle, "token": token, "is_public": d.get("is_public") is not False})
        if len(p) == 4 and p[:2] == ["v1", "users"] and p[3] in ("github", "slack"):
            d = self.body(); handle = p[2]
            if not c.execute("SELECT 1 FROM user WHERE handle=?", (handle,)).fetchone(): return self.send_json(404, {"error": "user not found"})
            if p[3] == "github":
                login = (d.get("github_login") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login): return self.send_json(400, {"error": "invalid github login"})
                c.execute("INSERT INTO link VALUES (?,?,(SELECT slack_user_id FROM link WHERE handle=?)) ON CONFLICT(handle) DO UPDATE SET github_login=excluded.github_login", (handle, login, handle))
                c.commit(); return self.send_json(200, {"handle": handle, "github_login": login, "github_url": f"https://github.com/{login}"})
            sid = (d.get("slack_user_id") or "").strip()
            if not sid: return self.send_json(400, {"error": "slack_user_id required"})
            c.execute("INSERT INTO link VALUES (?,(SELECT github_login FROM link WHERE handle=?),?) ON CONFLICT(handle) DO UPDATE SET slack_user_id=excluded.slack_user_id", (handle, handle, sid))
            c.commit(); return self.send_json(200, {"handle": handle, "slack_user_id": sid})
        if p == ["v1", "integrations", "github", "webhook"]:
            raw = self.raw(); secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
            if secret:
                expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, self.headers.get("X-Hub-Signature-256", "")): return self.send_json(401, {"error": "bad signature"})
            try: payload = json.loads(raw or b"{}")
            except Exception: return self.send_json(400, {"error": "invalid json"})
            event = self.headers.get("X-GitHub-Event", "push")
            if event != "push": return self.send_json(200, {"accepted": False, "event": event})
            repo_name = (payload.get("repository") or {}).get("full_name", "")
            commits = payload.get("commits") or []
            proj = None
            for r in c.execute("SELECT id, repo_url FROM project"):
                if r["repo_url"] and repo_name and repo_name.lower() in r["repo_url"].lower(): proj = r["id"]
            head = (payload.get("after") or "")[:40]
            c.execute("INSERT INTO github_event VALUES (?,?,?,?,?)", (str(uuid.uuid4()), proj, repo_name, len(commits), head)); c.commit()
            return self.send_json(202, {"accepted": True, "project_id": proj, "commits": len(commits), "head": head})
        if p == ["v1", "integrations", "slack", "command"]:
            form = {k: v[0] for k, v in parse_qs(self.raw().decode()).items()}
            secret = os.environ.get("SLACK_SIGNING_SECRET")
            if secret:
                ts = self.headers.get("X-Slack-Request-Timestamp", ""); sig = self.headers.get("X-Slack-Signature", "")
                base = f"v0:{ts}:".encode() + self.raw()
                expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, sig): return self.send_json(401, {"error": "bad signature"})
            text = (form.get("text") or "stats").strip(); slack_id = form.get("user_id", "")
            link = c.execute("SELECT handle FROM link WHERE slack_user_id=?", (slack_id,)).fetchone()
            parts = text.split(); handle = parts[1] if len(parts) > 1 else (link["handle"] if link else form.get("user_name", ""))
            u = c.execute("SELECT handle FROM user WHERE handle=?", (handle,)).fetchone()
            if not u: return self.send_json(200, {"response_type": "ephemeral", "text": f"no struggletime account for {handle or 'you'} yet. create one on the dashboard, then link slack."})
            st = stats_for(c, user=handle)
            return self.send_json(200, {"response_type": "ephemeral", "text": f"@{handle}: {st['weighted_hours']} weighted h · streak {st['current_streak_days']}d · {st['buckets']} buckets tracked passively"})
        if p == ["v1", "projects"]:
            d = self.body(); pid = (d.get("id") or "").strip(); name = (d.get("name") or "").strip()
            if not pid or not name: return self.send_json(400, {"error": "id and name required"})
            owner = self.auth_handle(c) or d.get("owner_handle") or d.get("owner")
            try:
                c.execute("INSERT INTO project VALUES (?,?,?,?,?,?)", (pid, owner, name, d.get("repo_url"), None, None)); c.commit()
            except sqlite3.IntegrityError: return self.send_json(409, {"error": "project exists"})
            return self.send_json(201, {"id": pid, "owner": owner, "name": name, "repo_url": d.get("repo_url")})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "git":
            d = self.body()
            if not c.execute("SELECT 1 FROM project WHERE id=?", (p[2],)).fetchone():
                c.execute("INSERT INTO project VALUES (?,?,?,?,?,?)", (p[2], d.get("owner"), p[2], d.get("repo_url"), d.get("head_from"), d.get("head_to")))
            else:
                c.execute("UPDATE project SET repo_url=?,head_from=?,head_to=? WHERE id=?",
                          (d.get("repo_url"), d.get("head_from"), d.get("head_to"), p[2]))
            c.commit(); return self.send_json(200, {"id": p[2], "repo_url": d.get("repo_url"), "head_from": d.get("head_from"), "head_to": d.get("head_to")})
        if p == ["v1", "buckets", "batch"]:
            d = self.body(); buckets = d.get("buckets", [])
            if not isinstance(buckets, list) or not buckets: return self.send_json(400, {"error": "buckets required"})
            for i, b in enumerate(buckets):
                if not isinstance(b, dict) or not isinstance(b.get("counts"), dict) or not isinstance(b.get("git"), dict) \
                   or not all(k in b for k in ("bucket_id","project_id","prev_hash","hash","bucket_secs","bucket_start_wall","mono_delta_ms")):
                    return self.send_json(400, {"error": f"bucket {i}: missing required fields"})
            project = buckets[0]["project_id"]
            row = c.execute("SELECT last_hash FROM chain_head WHERE project_id=?", (project,)).fetchone()
            prev = row["last_hash"] if row else GENESIS
            for i, b in enumerate(buckets):
                errs = validate_allowlist(b)
                if errs: return self.send_json(400, {"error": f"bucket {i}: schema allowlist violation", "details": errs})
            seen = {r[0]: r[1] for r in c.execute("SELECT bucket_id, hash FROM bucket WHERE project_id=?", (project,))}
            for i, b in enumerate(buckets):
                if b["bucket_id"] in seen and bucket_hash(b) != seen[b["bucket_id"]]:
                    return self.send_json(422, {"error": f"bucket {i}: hash mismatch (tampered replay of known bucket_id)"})
            fresh = [b for b in buckets if b["bucket_id"] not in seen]
            if len(fresh) != len(buckets):
                return self.send_json(409, {"error": "duplicate bucket_id", "duplicates": len(buckets) - len(fresh)})
            for i, b in enumerate(fresh):
                if b["prev_hash"] != prev: return self.send_json(422, {"error": f"bucket {i}: prev_hash mismatch (chain broken)"})
                if bucket_hash(b) != b["hash"]: return self.send_json(422, {"error": f"bucket {i}: hash mismatch (tampered)"})
                prev = b["hash"]
            total = 0.0; user = self.auth_handle(c) or d.get("user_id")
            srow = c.execute("SELECT silent_secs FROM silent_streak WHERE project_id=?", (project,)).fetchone()
            seq = weight_sequence(fresh, srow["silent_secs"] if srow else 0)
            from verify import is_silent
            silent_end = (srow["silent_secs"] if srow else 0)
            for b in fresh: silent_end = silent_end + b["bucket_secs"] if is_silent(b) else 0
            c.execute("INSERT INTO silent_streak VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET silent_secs=excluded.silent_secs", (project, silent_end))
            for b, (w, state) in zip(fresh, seq):
                ws = b["bucket_secs"] * w; total += ws
                c.execute("INSERT INTO bucket VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (b["bucket_id"], user, project, b["bucket_start_wall"], b["bucket_secs"], state, w, ws, b["hash"], b["prev_hash"], json.dumps(b)))
            c.execute("INSERT INTO chain_head VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET last_hash=excluded.last_hash", (project, prev)); c.commit()
            return self.send_json(201, {"accepted": len(fresh), "weighted_secs_added": total, "weighted_hours_added": round(total / 3600, 4)})
        if p == ["v1", "submissions"]:
            d = self.body()
            if not d.get("ai_declaration"): return self.send_json(400, {"error": "ai_declaration required"})
            rows = c.execute("SELECT * FROM bucket WHERE project_id=? AND bucket_id BETWEEN ? AND ? ORDER BY bucket_start_wall",
                             (d["project_id"], d["from_bucket"], d["to_bucket"])).fetchall()
            if not rows: return self.send_json(404, {"error": "no buckets in range"})
            claimed = sum(r["weighted_secs"] for r in rows)
            sid = str(uuid.uuid4()); fl = flags([json.loads(r["payload"]) for r in rows], claimed / 3600)
            proj = c.execute("SELECT * FROM project WHERE id=?", (d["project_id"],)).fetchone()
            if proj and not proj["repo_url"]: fl.append("git_mismatch: no repo linked")
            who = self.auth_handle(c) or d.get("user_id")
            c.execute("INSERT INTO submission VALUES (?,?,?,?,?,?,?)",
                      (sid, who, d["project_id"], claimed, d["ai_declaration"], "pending", json.dumps(fl)))
            notify(c, f"new submission: {d['project_id']} · {round(claimed/3600, 2)} weighted h by {who} · pending review")
            c.commit()
            ev = len(json.dumps([json.loads(r["payload"]) for r in rows]).encode())
            return self.send_json(201, {"id": sid, "claimed_weighted_secs": claimed, "claimed_weighted_hours": round(claimed / 3600, 4), "status": "pending", "flags": fl, "evidence_bytes": ev, "evidence_kb": round(ev / 1024, 1)})
        if len(p) == 4 and p[:2] == ["v1", "submissions"] and p[3] == "review":
            d = self.body()
            if d.get("reason_code") not in REASONS: return self.send_json(400, {"error": "invalid reason_code", "allowed": sorted(REASONS)})
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            approved = float(d["approved_weighted_secs"])
            status = "approved" if approved >= r["claimed_weighted_secs"] else ("rejected" if approved == 0 else "deflated")
            c.execute("UPDATE submission SET status=? WHERE id=?", (status, p[2]))
            c.execute("INSERT INTO review VALUES (?,?,?,?,?)", (str(uuid.uuid4()), p[2], d.get("reviewer_id"), approved, d["reason_code"]))
            notify(c, f"submission {p[2][:8]} reviewed: {status} · {round(approved/3600, 2)} h approved · reason {d['reason_code']}")
            c.commit()
            return self.send_json(200, {"id": p[2], "status": status, "approved_weighted_secs": approved})
        return self.send_json(404, {"error": "not found"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18765"))
    print(f"listening on 127.0.0.1:{port} db={DB}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
