# Server

Stdlib-only Python + SQLite reference implementation. Production target
is the same API on Go/Rust + Postgres (`schemas/server.sql`).

```bash
PORT=18765 SERVER_DB=/tmp/final.db python3 server/server.py
# dashboard: http://127.0.0.1:18765
```

| Endpoint | Behaviour |
|---|---|
| `GET /` | Live dashboard (weighted h, streak, goal, flags, builders) |
| `POST /v1/users` | Create account, returns bearer token once; public by default, `is_public:false` opts out |
| `GET /v1/users` / `GET /v1/users/{handle}` | Public builder board / profile with totals, streak, projects (403 if private) |
| `POST /v1/projects` | Register project (token owner or `owner_handle`) |
| `POST /v1/projects/{id}/git` | Link repo + `head_from..head_to` anchor range |
| `POST /v1/users/{h}/github` · `GET /v1/projects/{id}/github` | Link github login; parsed repo + webhook anchors (optional live fetch with `?fetch=1`) |
| `POST /v1/integrations/github/webhook` | Push webhook; hmac verified when `GITHUB_WEBHOOK_SECRET` set; commit messages never stored |
| `POST /v1/users/{h}/slack` · `POST /v1/integrations/slack/command` | Link slack id; signed `/struggletime stats` slash command |
| `GET /v1/integrations/events` | Slack notifications (delivered via `SLACK_WEBHOOK_URL`, else queued here) |
| `POST /v1/buckets/batch` | Verify chain from stored head (422 tamper, 409 duplicate), weight, store |
| `GET /v1/projects/{id}/stats` | Raw vs weighted, per-state, flags, streak |
| `GET /v1/projects/{id}/stream?once=1` | SSE snapshot (omit `once` for keepalive stream) |
| `POST /v1/submissions` | AI declaration required; claims weighted secs; git/flag checks |
| `POST /v1/submissions/{id}/review` | Reason-coded approve / deflate / reject |

Tests: `python3 -m unittest discover -s tests` — 18 passing.
