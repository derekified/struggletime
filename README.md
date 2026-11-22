# StruggleTime

Tracks coding time, with more weight on fixing things. Fail → fix → pass
loops score higher than idle time or a big AI paste you never ran. The
server only stores allowlisted metadata: salted file IDs, counts, and
hashes. No screenshots, prompts, or source.

AGPLv3. Built for Hack Club projects.

https://github.com/derekified/struggletime

## Releases — App + CLI

Each [GitHub Release](https://github.com/derekified/struggletime/releases)
ships **one** `struggletime` / `struggletime.exe` used as both:

| Mode | How |
|---|---|
| Desktop tray app | Start Menu / Applications / `struggletime --tray` |
| CLI | `struggletime status`, `apikey`, `start`, … |

Same binary, same settings, same hash-chained ledger (`prev_hash`,
`sha256(salt:path)`).

### macOS Universal (Apple Silicon + Intel)

```bash
curl -fLO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-universal.tar.gz
curl -fLO https://github.com/derekified/struggletime/releases/latest/download/struggletime-macos-universal.tar.gz.sha256
shasum -a 256 -c struggletime-macos-universal.tar.gz.sha256
tar -xzf struggletime-macos-universal.tar.gz
sh struggletime-macos-universal/install.sh struggletime-macos-universal/struggletime
struggletime --version
open -a StruggleTime
```

**macOS Intel:** same steps with `struggletime-macos-intel.tar.gz`.

### Linux x86_64

```bash
curl -fLO https://github.com/derekified/struggletime/releases/latest/download/struggletime-linux-x86_64.tar.gz
tar -xzf struggletime-linux-x86_64.tar.gz
sh struggletime-linux-x86_64/install.sh struggletime-linux-x86_64/struggletime
struggletime --version
```

### Windows x64

Download `struggletime-windows-x64.zip`, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
struggletime --version
```

Full steps: [docs/installation](https://struggletime.screenseekr.com/docs/installation/).

## Install from source

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make rust
make install               # App + CLI
struggletime serve         # http://127.0.0.1:18765
struggletime apikey <st_…>
struggletime start --repo ~/my-project
struggletime status
struggletime adapters install   # VS Code/Cursor, Neovim, Claude, hooks, …
```

### Linux tray build deps

For `make rust-linux` / `--features linux-tray` (AppIndicator icon), install:

```bash
# Debian / Ubuntu
sudo apt-get install -y libxdo-dev libgtk-3-dev libayatana-appindicator3-dev pkg-config
```

Fedora: `libxdo-devel gtk3-devel libayatana-appindicator-gtk3-devel pkgconf-pkg-config`.  
Arch: `xdotool gtk3 libayatana-appindicator pkgconf`.

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
| `scripts/install.sh` / `install.ps1` | App + CLI installers |
| `adapters/` | optional editor / agent bridges |
| `server/static/` | dashboard SPA |
| `docs-site/` | Fumadocs → `/docs` |
| `weighting/` | rules YAML + calibration notes |
| `schemas/` | allowlist + SQL |

## Make

```bash
make rust              # release binary
make install           # desktop app + CLI on PATH
make check             # cargo test + adapter verify
make clippy
make server            # struggletime serve
make docs              # build /docs
```

## License

AGPLv3. See `LICENSE`.
