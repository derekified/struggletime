# StruggleTime

Tracks coding time, with more weight on fixing things. Fail → fix → pass
loops score higher than idle time or a big AI paste you never ran. The
server only stores allowlisted metadata: salted file IDs, counts, and
hashes. No screenshots, prompts, or source.

AGPLv3. Built for Hack Club projects.

https://github.com/derekified/struggletime

## Releases & prebuilt binaries

Download a single OS binary from
[GitHub Releases](https://github.com/derekified/struggletime/releases)
(built on `v*` tags). One `struggletime` / `struggletime.exe` includes:

| Built-in | Command |
|---|---|
| Tray supervisor | `struggletime tray` |
| CLI | `struggletime status`, `start`, `apikey`, … |
| Background tracker | `struggletime daemon` |
| Local HTTP server | `struggletime serve` |

### Quick install

**macOS Universal** (Apple Silicon + Intel)

```bash
curl -LO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-universal.tar.gz
curl -LO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-universal.tar.gz.sha256
shasum -a 256 -c struggletime-macos-universal.tar.gz.sha256
tar -xzf struggletime-macos-universal.tar.gz
chmod +x struggletime-macos-universal/struggletime
sudo install -m 755 struggletime-macos-universal/struggletime /usr/local/bin/struggletime
```

**macOS Intel:** same steps with `struggletime-macos-intel.tar.gz`.

**Windows x64:** download `struggletime-windows-x64.exe`, check it against
`SHA256SUMS.txt` or the `.sha256` file, then put it on your PATH as
`struggletime.exe`.

Every release includes `SHA256SUMS.txt`. More detail:
[docs/installation](https://62-83-116-143.sslip.io/docs/installation/).

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

You do not need a scripting-language runtime.

## What it does

A local tracker watches file saves in linked project folders. Paths leave as
`sha256(salt:path)`. Each sealed bucket points at the previous one with
`prev_hash`. Scoring lives in `rust/crates/st-core`.

Optional IDE and agent adapters live under `adapters/`.

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

AGPLv3. See `LICENSE`.
