# API

base: `http://127.0.0.1:18765` · json everywhere · bearer token from
account creation (`Authorization: Bearer <token>`) where noted.

## accounts and profiles

| route | what |
|---|---|
| `POST /v1/users` `{handle, is_public?}` | create account, returns token once. 409 if taken |
| `GET /v1/users` | public builders with totals, streaks, github links |
| `GET /v1/users/{handle}` | public profile (403 if private and not you) |
| `POST /v1/users/{handle}/github` `{github_login}` | link github |
| `POST /v1/users/{handle}/slack` `{slack_user_id}` | link slack |

## projects and time

| route | what |
|---|---|
| `POST /v1/projects` `{id, name, repo_url?}` | register a project (token sets owner) |
| `GET /v1/projects` | all projects with weighted/raw stats and flags |
| `POST /v1/projects/{id}/git` `{repo_url, head_from, head_to}` | link repo + anchor range |
| `POST /v1/buckets/batch` `{user_id?, buckets:[…]}` | ingest sealed buckets. 400 schema allowlist violation, 409 identical replay, 422 tamper or broken chain |
| `GET /v1/projects/{id}/stats` | buckets, raw vs weighted, per-state counts, streak, flags |
| `GET /v1/projects/{id}/buckets` | the stored metadata buckets + evidence size |
| `GET /v1/projects/{id}/friction` | friction timeline + commit anchors (review view) |
| `GET /v1/projects/{id}/stream?once=1` | sse stats snapshot (omit `once` for a live stream) |
| `GET /v1/projects/{id}/github` | parsed repo link + webhook anchors (`?fetch=1` for live github data) |
| `GET /v1/overview` | instance-wide totals for the dashboard |

bucket format: [`schemas/heartbeat.schema.json`](../schemas/heartbeat.schema.json).
anything outside that allowlist (prompt text, commands, code, paths)
is rejected before chain verification.

## submissions and review

| route | what |
|---|---|
| `POST /v1/submissions` `{project_id, from_bucket, to_bucket, ai_declaration}` | freeze an evidence pack. declaration is required. returns claimed weighted time + evidence size |
| `GET /v1/submissions` · `GET /v1/submissions/{id}` | list / fetch |
| `POST /v1/submissions/{id}/review` `{approved_weighted_secs, reason_code, reviewer_id}` | approve, deflate or reject. reason code must be one of `NONE, UNATTENDED_AGENT_DUMP, IDLE_INFLATION, NO_TEST_ITERATION, BOILERPLATE_HEAVY, CHAIN_BROKEN, GIT_MISMATCH` |

## integrations

| route | what |
|---|---|
| `POST /v1/integrations/github/webhook` | signed push events (see [integrations.md](integrations.md)) |
| `POST /v1/integrations/slack/command` | signed `/struggletime stats` |
| `GET /v1/integrations/events` | queued slack notifications |

## errors

errors are `{ "error": "..." }` with 400 (malformed / allowlist),
401 (bad integration signature), 403 (private profile), 404, 409
(duplicate), 422 (chain broken / tampered). the server answers
malformed input with a 400 and stays up. fuzz it and see.
