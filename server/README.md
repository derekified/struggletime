# Server (test slice)

Stdlib-only Python + SQLite reference server. Production target is the
same API on Go/Rust + Postgres (`schemas/server.sql`); SQLite here keeps
the test slice zero-dependency.

```bash
PORT=18765 SERVER_DB=/tmp/test-server.db python3 server/server.py
curl localhost:18765/health
```

| Endpoint | Behaviour |
|---|---|
| `POST /v1/buckets/batch` | Verifies hash chain continuing from stored head; 422 on tamper/broken chain, 409 on duplicates; applies `weighting/rules-v0.1.0.yaml`; returns weighted seconds added |
| `GET /v1/projects/{id}/stats` | Buckets, raw vs weighted secs/hours, per-state counts, outlier flags (404 if unknown) |
| `POST /v1/submissions` | Requires `ai_declaration`; claims weighted secs for bucket range; returns flags |
| `POST /v1/submissions/{id}/review` | Validates reason code; sets approved/deflated/rejected from approved secs |
| `GET /v1/submissions/{id}` | Submission + flags |
| `GET /health` | `{"ok": true}` |

Not yet: auth, public profiles, git-graph overlay, SSE. Those sit behind
this ingest/verify contract and do not change it.
