# Troubleshooting

**dashboard is empty**
the daemon hasn't sealed a bucket yet (first one lands after
`AITIME_BUCKET_SECS`, default 5 min), or it's pointing somewhere else.
check both use the same port, then `make inspect REPO=~/my-project` to
see pending buckets locally.

**422 chain broken / prev_hash mismatch**
your ledger and the server disagree, usually because the server db was
reset, or the same project id is tracked from two machines. use a
fresh project id per machine, or reset both sides together. this error
is the tamper evidence working as intended. don't silence it.

**422 tampered / 400 allowlist**
a bucket was modified after sealing, or something tried to send a field
outside the schema (paths, prompt text, commands). run
`daemon/tracker_inspect.py` locally; it shows the offending bucket byte-for-byte.

**github webhook 401**
`GITHUB_WEBHOOK_SECRET` on the server ≠ the secret in the repo webhook
settings. they must match exactly, and the content type must be
`application/json`.

**slack command 401 / no reply**
signing secret mismatch (use the one on the app's *basic information*
page, not the client secret), or your slack id isn't linked:
`POST /v1/users/you/slack`. unsigned local testing works with no secret
set on either side.

**slack notifications not arriving**
no `SLACK_WEBHOOK_URL` → they are queued, by design. read them at
`GET /v1/integrations/events`; `delivered: 0` there means queue, not loss.

**my reading/debugging time counts at 0.7× then fades**
that's the silent-debug decay (5–12 min), documented in
[architecture.md](architecture.md). run or edit something and it
resets instantly. if real work is being undercounted for you, that is
calibration data. say so, with an inspect dump, instead of working
around it.

**windows: hook does nothing**
you need to dot-source `hooks.ps1` from your `$PROFILE` in every new
session, and the daemon must be running separately. the filesystem half
works with no hook at all.

**port already in use**
`PORT=18766 make server` (and point the daemon at the same port).
