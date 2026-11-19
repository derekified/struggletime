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

full env list: [`.env.example`](../.env.example). the server binds
`127.0.0.1` only, on purpose. nothing reaches it except through a
tunnel or a reverse proxy on the same box (below).

## vps setup, step by step

```bash
# on the vps
git clone <this repo> struggletime && cd struggletime
cp .env.example .env        # fill in what you have, see below
mkdir -p /srv/struggletime  # or point SERVER_DB somewhere writable
```

in `.env` on the vps:

```bash
PORT=18765
SERVER_DB=/srv/struggletime/server.db
OAUTH_REDIRECT_BASE=https://your-domain.example   # or leave 127.0.0.1 for tunnel-only testing
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
```

run it once by hand first (`make server`, open it through the tunnel
below, claim your handle), then keep it alive with systemd:

```ini
# /etc/systemd/system/struggletime.service
[Unit]
Description=StruggleTime server
After=network.target
[Service]
WorkingDirectory=/home/you/struggletime
ExecStart=/usr/bin/python3 server/server.py
Restart=on-failure
User=you
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now struggletime
journalctl -u struggletime -f   # logs
```

## port forward it

three ways, in the order you will probably want them.

**1. ssh tunnel, private testing (no public exposure at all).** from
your own machine:

```bash
ssh -L 18765:127.0.0.1:18765 you@your-vps
```

leave that open, then `http://127.0.0.1:18765` in your browser is the
vps dashboard, and your local daemon/tray points at
`http://127.0.0.1:18765` as usual. oauth and webhooks cannot reach a
tunnel like this, so use it for dashboard and tracking tests.

**2. reverse proxy + tls, public (what oauth and webhooks need).**
point a domain at the vps, then caddy is two lines:

```
your-domain.example {
    reverse_proxy 127.0.0.1:18765
}
```

```bash
sudo caddy reload --config /etc/caddy/Caddyfile   # or systemctl restart caddy
```

set `OAUTH_REDIRECT_BASE=https://your-domain.example`, restart the
server, and register the callback urls from
[setup](setup.md#secrets-optional-for-slackgithub-integrations)
with exactly that domain. keep port 18765 firewalled off
(`ufw allow OpenSSH`, `ufw allow 80,443/tcp`, nothing else) — the
proxy is the only public door.

**3. temporary public tunnel from the vps** (quick oauth test, no
domain yet):

```bash
ngrok http 18765        # or: cloudflared tunnel --url http://127.0.0.1:18765
```

use the https address it gives you as `OAUTH_REDIRECT_BASE` and in
the oauth app callbacks, and redo both whenever the tunnel restarts
and the address changes.

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
