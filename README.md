# StruggleTime

Tracks coding time, but weights it toward actually fixing things — fail →
fix → pass loops get more credit than idle time or dumping a huge AI paste
you never ran. The server only gets small allowlisted metadata (scrambled
file IDs, counts, hashes). No screenshots, prompts, or source.

AGPLv3. Aimed at Hack Club projects.

https://github.com/derekified/struggletime

## Releases & prebuilt binaries

Grab a single OS binary from
[GitHub Releases](https://github.com/derekified/struggletime/releases)
(`v*` tags). One `struggletime` / `struggletime.exe` includes:

| Built-in | Command |
|---|---|
| Tray supervisor | `struggletime tray` |
| CLI | `struggletime status`, `start`, `apikey`, … |
| Background tracker | `struggletime daemon` |
| Local HTTP server | `struggletime serve` |

### Quick install

**macOS (Apple Silicon)**

```bash
curl -LO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-aarch64.tar.gz
curl -LO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-aarch64.tar.gz.sha256
shasum -a 256 -c struggletime-macos-aarch64.tar.gz.sha256
tar -xzf struggletime-macos-aarch64.tar.gz
sudo install -m 755 struggletime-macos-aarch64/struggletime /usr/local/bin/struggletime
```

**Windows** — download `struggletime-windows-x86_64.zip`, verify the SHA-256,
unzip, and put `struggletime.exe` on your PATH.

Each release also ships `SHA256SUMS.txt`. Intel macOS and Linux x86_64
archives are published the same way. See [docs/installation](https://62-83-116-143.sslip.io/docs/installation/).

## Install from source

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make rust
make install-binaries      # ~/.local/bin/struggletime
struggletime serve         # http://127.0.0.1:18765
struggletime apikey <st_…>
struggletime start --repo ~/my-project
struggletime status
```

No scripting-language runtime required.

## What it does

A local tracker watches file saves in linked project folders. Paths leave as
`sha256(salt:path)`. Each sealed bucket points at the previous one with
`prev_hash`. Scoring lives in `rust/crates/st-core`.

Optional IDE/agent adapters live under `adapters/`.

## Layout

| Path | |
|---|---|
| `rust/` | unified `struggletime` binary (`make rust`) |
| `daemon/` | installers, shell hooks, assets |
| `adapters/` | optional editor / agent bridges |
| `server/static/` | dashboard SPA |
| `docs-site/` | Fumadocs → `/docs` |
| `weighting/` | rules YAML + calibration notes |
| `schemas/` | allowlist + SQL |

## Make

```bash
make rust              # release binary
make install-binaries  # link into ~/.local/bin
make check             # cargo test + adapter verify
make clippy
make server            # struggletime serve
make docs              # build /docs
```

## License

AGPLv3 — see `LICENSE`.
