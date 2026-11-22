# StruggleTime

Tracks coding time, but weights it toward actually fixing things — fail →
fix → pass loops get more credit than idle time or dumping a huge AI paste
you never ran. The server only gets small allowlisted metadata (scrambled
file IDs, counts, hashes). No screenshots, prompts, or source.

AGPLv3. Aimed at Hack Club projects.

https://github.com/derekified/struggletime

## Native binaries (no Python)

Daemon, server, helper, and tray are written in Rust under `rust/`.

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make rust                  # needs a Rust toolchain
make install-binaries      # symlinks into ~/.local/bin
struggletime-server        # dashboard on :18765 (serves server/static)
struggletime-helper apikey <st_…>
struggletime-helper start --repo ~/my-project
struggletime-helper status
```

Windows / macOS / Linux release zips are built by GitHub Actions on version
tags (`v*`) — download from
[Releases](https://github.com/derekified/struggletime/releases), unzip, put
the four binaries on your PATH. No Python install required.

## Source (Python reference)

The original stdlib Python daemon/server under `daemon/` and `server/` remain
for tests, adapters, and calibration:

```bash
make check
make server
sh daemon/install.sh
```

## What it does

A local tracker watches file saves in linked project folders. Paths leave as
`sha256(salt:path)`. Each sealed bucket points at the previous one with
`prev_hash`. Scoring lives in Rust (`st-core`) and the Python mirror
`src/verify.py` (same formula version).

Optional IDE/agent adapters live under `adapters/`.

## Layout

| Path | |
|---|---|
| `rust/` | native daemon, server, helper, tray (`make rust`) |
| `daemon/` | Python tracker + installers (reference) |
| `adapters/` | optional editor / agent bridges |
| `server/` | Python API + dashboard SPA assets (`server/static/`) |
| `src/` | Python scoring mirror (`verify.py`) |
| `weighting/` | rules YAML + calibration notes |
| `docs-site/` | Fumadocs → static `/docs` |
| `tests/` | Python unittest suite (`make check`) |

## Make

```bash
make rust              # release native binaries
make install-binaries  # link into ~/.local/bin
make rust-test         # Rust unit tests
make check             # Python tests + adapter verify
make server            # Python API + dashboard
make docs              # build /docs
```

## License

AGPLv3 — see `LICENSE`.
