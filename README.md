# StruggleTime

Tracks coding time, but weights it toward actually fixing things — fail →
fix → pass loops get more credit than idle time or dumping a huge AI paste
you never ran. The server only gets small allowlisted metadata (scrambled
file IDs, counts, hashes). No screenshots, prompts, or source.

AGPLv3. Aimed at Hack Club projects.

https://github.com/derekified/struggletime

## Install (native binaries)

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make rust
make install-binaries      # ~/.local/bin
struggletime-server        # http://127.0.0.1:18765
struggletime-helper apikey <st_…>
struggletime-helper start --repo ~/my-project
struggletime-helper status
```

Or download a Windows / macOS / Linux zip from
[Releases](https://github.com/derekified/struggletime/releases) (built on
`v*` tags). No scripting-language runtime required.

## What it does

A local tracker watches file saves in linked project folders. Paths leave as
`sha256(salt:path)`. Each sealed bucket points at the previous one with
`prev_hash`. Scoring lives in `rust/crates/st-core`.

Optional IDE/agent adapters live under `adapters/`.

## Layout

| Path | |
|---|---|
| `rust/` | daemon, server, helper, tray (`make rust`) |
| `daemon/` | installers, shell hooks, assets |
| `adapters/` | optional editor / agent bridges |
| `server/static/` | dashboard SPA |
| `docs-site/` | Fumadocs → `/docs` |
| `weighting/` | rules YAML + calibration notes |
| `schemas/` | allowlist + SQL |

## Make

```bash
make rust              # release binaries
make install-binaries  # link into ~/.local/bin
make check             # cargo test + adapter verify
make clippy
make server            # struggletime-server
make docs              # build /docs
```

## License

AGPLv3 — see `LICENSE`.
