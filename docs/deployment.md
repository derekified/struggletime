# Deployment

One small vps is the whole architecture. at struggletime scale
(15,488 monthly users, ~110k hours/month) clients pre-roll time into
5-minute buckets, so the server sees roughly 0.5 writes/second.

## sizing (measured, not estimated)

- 3,000 hash-chained buckets ingest + verify in ~0.10s locally
  (~30k/sec) against a ~0.5/sec requirement
- server idles at tens of mb of ram
- evidence packs are kb, object storage is a rounding error

budget: a $40–60/mo vps covers it with huge headroom; the bounty cap is
$500/mo. production persistence should use the postgres schema in
[`schemas/server.sql`](../schemas/server.sql) (monthly-partitioned
buckets) behind the same api. the reference server in `server/` stores
to sqlite, which is fine for pilots and self-hosting.

## run it

```bash
PORT=18765 SERVER_DB=/srv/struggletime.db \
GITHUB_WEBHOOK_SECRET=... SLACK_SIGNING_SECRET=... SLACK_WEBHOOK_URL=... \
python3 server/server.py
```

put it behind your normal reverse proxy for tls, and expose only the
dashboard/api port. full env list: [`.env.example`](../.env.example).

## operations

- **backups:** copy the database file (or `pg_dump`). buckets are
  append-only; the chain head per project is in the same backup.
- **upgrades:** pull, restart. schema creation is `if not exists`;
  bucket payloads are versioned (`v`) and formula versions are stored
  per bucket, so old evidence stays interpretable.
- **watch:** `GET /health`, `GET /v1/overview` (totals + pending
  reviews), `GET /v1/integrations/events` (undelivered slack = 0 in the
  queue is a smell if a webhook is configured).
- **trust knobs:** reviewer reason codes and the formula live in
  [`weighting/rules-v0.1.0.yaml`](../weighting/rules-v0.1.0.yaml).
  changing weights means bumping the formula version, never editing
  history: old buckets keep the version they were scored under.
