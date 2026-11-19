# Integrations

Both integrations are optional, work self-hosted, and degrade
gracefully: configure nothing and everything still runs.

## github

### link a user

dashboard form, or:

```bash
curl -X POST localhost:18765/v1/users/you/github \
  -H 'Content-Type: application/json' -d '{"github_login":"your-name"}'
```

shown on your public profile and in the builders list.

### repo webhooks (push → commit anchors)

1. on your repo: settings → webhooks → add webhook
2. payload url: `https://your-server/v1/integrations/github/webhook`
3. content type `application/json`, events: just `push`
4. set a secret, and start the server with the same value:
   `GITHUB_WEBHOOK_SECRET=... python3 server/server.py`

what happens: the signature is verified (bad signature → 401), the
push is matched to the project whose repo url matches, and the push is
stored as a commit anchor — commit **count and head sha only**, commit
messages are never stored. anchors appear on
`GET /v1/projects/{id}/github` and are what reviewers cross-check your
tracked buckets against. with no secret set, webhooks are accepted
unverified (fine locally, not on the internet).

### live repo lookup (optional)

`GET /v1/projects/{id}/github?fetch=1` fetches stars/branch/last push
from the github api. set `GITHUB_TOKEN` to raise rate limits. off
unless you ask for it — the server never phones home on its own.

## slack

### notifications

set `SLACK_WEBHOOK_URL` (slack app → incoming webhooks) before starting
the server. new submissions and finished reviews post to that channel.
not set? the same messages queue locally and stay readable at
`GET /v1/integrations/events`. nothing is silently dropped either way.

### slash command: /struggletime stats

1. create a slack app → slash commands → `/struggletime`
2. request url: `https://your-server/v1/integrations/slack/command`
3. link yourself once (dashboard form or API):
   `POST /v1/users/you/slack {"slack_user_id":"U0123ABCD"}`
4. start the server with `SLACK_SIGNING_SECRET=<signing secret from
   the app's basic information page>`

`/struggletime stats` replies (only to you) with your weighted hours,
streak and bucket count. `/struggletime stats somehandle` looks up a
handle directly. forged signatures get a 401; with no secret set the
command works unsigned for local testing.
