#!/usr/bin/env python3
import json, os, sqlite3, sys, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from verify import bucket_hash, weight, flags, GENESIS

REASONS = {"UNATTENDED_AGENT_DUMP","IDLE_INFLATION","NO_TEST_ITERATION","BOILERPLATE_HEAVY","CHAIN_BROKEN","GIT_MISMATCH","NONE"}
DB = os.environ.get("SERVER_DB", "server.db")

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
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
    """)
    return c

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        p = urlparse(self.path).path.strip("/").split("/")
        c = db()
        if p == ["health"]:
            return self.send_json(200, {"ok": True})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "stats":
            rows = c.execute("SELECT * FROM bucket WHERE project_id=?", (p[2],)).fetchall()
            if not rows: return self.send_json(404, {"error": "project not found"})
            buckets = [json.loads(r["payload"]) for r in rows]
            weighted = sum(r["weighted_secs"] for r in rows)
            by_state = {}
            for r in rows: by_state[r["state"]] = by_state.get(r["state"], 0) + 1
            return self.send_json(200, {"project_id": p[2], "buckets": len(rows),
                "raw_secs": sum(r["bucket_secs"] for r in rows), "weighted_secs": weighted,
                "weighted_hours": round(weighted / 3600, 4), "by_state": by_state,
                "flags": flags(buckets, weighted / 3600)})
        if len(p) == 3 and p[:2] == ["v1", "submissions"]:
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            return self.send_json(200, {**dict(r), "flags": json.loads(r["flags"])})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path.strip("/").split("/"); c = db()
        if p == ["v1", "buckets", "batch"]:
            d = self.body(); buckets = d.get("buckets", [])
            if not buckets: return self.send_json(400, {"error": "buckets required"})
            project = buckets[0]["project_id"]
            row = c.execute("SELECT last_hash FROM chain_head WHERE project_id=?", (project,)).fetchone()
            prev = row["last_hash"] if row else GENESIS
            seen = {r[0] for r in c.execute("SELECT bucket_id FROM bucket WHERE project_id=?", (project,))}
            fresh = [b for b in buckets if b["bucket_id"] not in seen]
            if len(fresh) != len(buckets):
                return self.send_json(409, {"error": "duplicate bucket_id", "duplicates": len(buckets) - len(fresh)})
            for i, b in enumerate(fresh):
                if b["prev_hash"] != prev:
                    return self.send_json(422, {"error": f"bucket {i}: prev_hash mismatch (chain broken)"})
                if bucket_hash(b) != b["hash"]:
                    return self.send_json(422, {"error": f"bucket {i}: hash mismatch (tampered)"})
                prev = b["hash"]
            total = 0.0
            for b in fresh:
                w, state = weight(b); ws = b["bucket_secs"] * w; total += ws
                c.execute("INSERT INTO bucket VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (b["bucket_id"], d.get("user_id"), project, b["bucket_start_wall"],
                     b["bucket_secs"], state, w, ws, b["hash"], b["prev_hash"], json.dumps(b)))
            c.execute("INSERT INTO chain_head VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET last_hash=excluded.last_hash",
                      (project, prev)); c.commit()
            return self.send_json(201, {"accepted": len(fresh), "weighted_secs_added": total,
                                        "weighted_hours_added": round(total / 3600, 4)})
        if p == ["v1", "submissions"]:
            d = self.body()
            if not d.get("ai_declaration"): return self.send_json(400, {"error": "ai_declaration required"})
            rows = c.execute("SELECT * FROM bucket WHERE project_id=? AND bucket_id BETWEEN ? AND ? ORDER BY bucket_start_wall",
                             (d["project_id"], d["from_bucket"], d["to_bucket"])).fetchall()
            if not rows: return self.send_json(404, {"error": "no buckets in range"})
            claimed = sum(r["weighted_secs"] for r in rows)
            sid = str(uuid.uuid4()); fl = flags([json.loads(r["payload"]) for r in rows], claimed / 3600)
            c.execute("INSERT INTO submission VALUES (?,?,?,?,?,?,?)",
                      (sid, d.get("user_id"), d["project_id"], claimed, d["ai_declaration"], "pending", json.dumps(fl)))
            c.commit()
            return self.send_json(201, {"id": sid, "claimed_weighted_secs": claimed,
                                        "claimed_weighted_hours": round(claimed / 3600, 4), "status": "pending", "flags": fl})
        if len(p) == 4 and p[:2] == ["v1", "submissions"] and p[3] == "review":
            d = self.body()
            if d.get("reason_code") not in REASONS: return self.send_json(400, {"error": "invalid reason_code", "allowed": sorted(REASONS)})
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            approved = float(d["approved_weighted_secs"])
            status = "approved" if approved >= r["claimed_weighted_secs"] else ("rejected" if approved == 0 else "deflated")
            c.execute("UPDATE submission SET status=? WHERE id=?", (status, p[2]))
            c.execute("INSERT INTO review VALUES (?,?,?,?,?)", (str(uuid.uuid4()), p[2], d.get("reviewer_id"), approved, d["reason_code"]))
            c.commit()
            return self.send_json(200, {"id": p[2], "status": status, "approved_weighted_secs": approved})
        return self.send_json(404, {"error": "not found"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18765"))
    print(f"listening on 127.0.0.1:{port} db={DB}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
