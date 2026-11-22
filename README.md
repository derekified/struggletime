# StruggleTime

Tracks coding time, but weights it toward actually fixing things — fail →
fix → pass loops get more credit than idle time or dumping a huge AI paste
you never ran. The server only gets small allowlisted metadata (scrambled
file IDs, counts, hashes). No screenshots, prompts, or source.

Python 3.10+, stdlib only for the daemon and server. AGPLv3. Aimed at Hack
Club projects.

https://github.com/derekified/struggletime

## Run it

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make check
make server                    # http://127.0.0.1:18765
sh daemon/install.sh           # once
struggletime-helper apikey <st_…>   # from Settings
struggletime-helper start --repo ~/my-project
struggletime-helper status
```

Demo seed: `make demo` (port 18766). Docs: `make docs`, then open `/docs/`
on the server.

## What it does

A local daemon watches file saves and git activity in linked project
folders. Paths leave as `sha256(salt:path)`. Each sealed bucket points at
the previous one with `prev_hash`, so rewriting old history shows up in
review. Daemon and server both score with `src/verify.py`.

Optional IDE/agent adapters live under `adapters/` (VS Code family,
Neovim, JetBrains, Godot, Unity, Claude Code, Codex, OpenClaw, …). You
don’t need them — process labels and filesystem/git signals are enough to
count time.

## Layout

| Path | |
|---|---|
| `daemon/` | tracker, helper CLI, tray, installers, shell hooks |
| `adapters/` | optional editor / agent bridges |
| `server/` | HTTP API + dashboard (`server/static/`) |
| `src/` | shared scoring (`verify.py`) |
| `weighting/` | rules YAML + calibration notes |
| `docs-site/` | Fumadocs → static `/docs` (`make docs`) |
| `docs/` | short markdown notes |
| `schemas/` | allowlist + SQL |
| `scripts/` | demo, packaging, adapter checks |
| `tests/` | `make check` |

## Make

```bash
make check             # tests + adapter verify
make server            # API + dashboard
make docs              # build /docs
make tray              # menu bar / system tray
make verify-adapters
```

## License

AGPLv3 — see `LICENSE`.
