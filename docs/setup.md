# Setup

Requirements: python 3.10+, git. Nothing else — no pip installs, no node,
no database to run. Works on windows, mac and linux.

## 1. check your machine

```bash
git clone <this repo> && cd struggletime
make check        # python, git, full test suite
```

## secrets (optional, for slack/github integrations)

`git pull` will never bring secret values down. that is deliberate:
they must not live in git. for local use, copy the template and fill
it in. `.env` is gitignored and cannot be committed by accident:

```bash
cp .env.example .env   # then fill in the values you have
```

the server loads `.env` automatically on start. real exported
environment variables win over the file. hosted deployments should use
the platform's secret store instead of a file.

## 2. start the server

```bash
make server       # http://127.0.0.1:18765
```

Open it. You get the dashboard: stats, friction timeline, projects,
builders, review queue. It is empty until the daemon sends something.

Create your account (token is shown once, save it):

```bash
curl -X POST localhost:18765/v1/users -H 'Content-Type: application/json' -d '{"handle":"you"}'
```

Profiles are public by default. Pass `"is_public": false` to opt out.

## 3. track a project

Terminal 2:

```bash
sh daemon/install.sh   # once, per machine
struggletime-helper start --repo ~/my-project --user you
struggletime-helper status   # logs: struggletime-helper logs
```

That is it. Code like normal. Every 5 minutes the daemon seals a
metadata bucket and sends it; the dashboard updates within seconds.
There is no start/stop — closing the daemon is the only off switch.

Add the shell hook so your test/build/git commands and ai cli launches
count with exit codes (counts only, commands never leave your machine):

```bash
# mac / linux (bash or zsh), add to .bashrc / .zshrc
source /path/to/struggletime/daemon/hooks.sh
```

```powershell
# windows: add to your $PROFILE
. C:\path\to\struggletime\daemon\hooks.ps1
```

## 4. see exactly what leaves your machine (do this once)

```bash
make inspect REPO=~/my-project
```

It prints every pending bucket, its size in kb, chain validity, and
whether it passes the schema allowlist. That output is byte-for-byte
what the server receives. If you don't like what you see, don't run the
daemon — that's the deal.

## 5. submit hours for review

Logged hours are private. Submitting is a separate, explicit step:

1. link the repo from the dashboard project or
   `POST /v1/projects/{id}/git`
2. submit a bucket range with an honest ai declaration
   (`POST /v1/submissions`, see [api.md](api.md))
3. a reviewer approves or deflates it in the dashboard review queue,
   with a reason code

## 6. just want to look around?

```bash
make demo         # seeded server on http://127.0.0.1:18766
```

Two demo builders, one real struggle session, one agent dump, and a
submission waiting in the review queue.

## where things live on your machine

| thing | where | leaves the machine? |
|---|---|---|
| raw events (file changes, commands) | memory + `~/.aitime/exec.jsonl` | never |
| local ledger + file-id salt | `<repo>/.aitime-ledger.json` (gitignored) | never |
| sealed 5-min metadata buckets | sent to the server you configured | yes, that's the product |
| screen, keystrokes, prompts, code | never recorded anywhere | — |

Next: [daemon.md](daemon.md) · [integrations.md](integrations.md) ·
[deployment.md](deployment.md) · [troubleshooting.md](troubleshooting.md)
