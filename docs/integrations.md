# Integrations

Both integrations are optional, work self-hosted, and degrade
gracefully: configure nothing and everything still runs.

## github

### connect with oauth

1. github → settings → developer settings → oauth apps → new oauth app
2. authorization callback url: `https://your-server/v1/auth/github/callback`
   (locally: `http://127.0.0.1:18765/v1/auth/github/callback`)
3. start the server with `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET`
   (env, `.env`, or the platform secret store — never committed)
4. in struggletime: settings → claim/sign in with your handle →
   "sign in with github". you are redirected to github, approve, and
   land back on settings linked.

the server exchanges the one-time code, reads your github login, and
discards the oauth token. only the username is stored. the manual
endpoint (`POST /v1/users/{handle}/github`) still exists for tests and
automation. shown on your public profile and in the builders list.

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

### connect with oauth + slash command

for identity: slack app → oauth & permissions → redirect url
`https://your-server/v1/auth/slack/callback`, then start the server
with `SLACK_CLIENT_ID` + `SLACK_CLIENT_SECRET`. in struggletime:
settings → "sign in with slack" → approve on slack → back, linked.
the code exchange happens server-side; only your slack user id is kept.

1. in the same slack app: slash commands → `/struggletime`
2. request url: `https://your-server/v1/integrations/slack/command`
3. (automation fallback: `POST /v1/users/you/slack {"slack_user_id":"U0123ABCD"}`)
4. start the server with `SLACK_SIGNING_SECRET=<signing secret from
   the app's basic information page>`

`/struggletime stats` replies (only to you) with your weighted hours,
streak and bucket count. `/struggletime stats somehandle` looks up a
handle directly. forged signatures get a 401; with no secret set the
command works unsigned for local testing.
