# StruggleTime Rust workspace

Single binary `struggletime` (crate `crates/struggletime`) bundles tray, CLI,
daemon, and local HTTP server.

```bash
cargo build --release -p struggletime
# Linux AppIndicator tray (needs system libs — see below):
cargo build --release -p struggletime --features linux-tray
./target/release/struggletime --help
./target/release/struggletime --tray
```

### Linux system packages (`linux-tray`)

`st-tray` enables `tray-icon` via the `linux-tray` feature. That crate links
`libxdo`, GTK3, and Ayatana AppIndicator:

| Distro | Packages |
|---|---|
| Debian / Ubuntu | `libxdo-dev libgtk-3-dev libayatana-appindicator3-dev pkg-config` |
| Fedora | `libxdo-devel gtk3-devel libayatana-appindicator-gtk3-devel pkgconf-pkg-config` |
| Arch | `xdotool gtk3 libayatana-appindicator pkgconf` |

Without `libxdo-dev`, the linker fails with `unable to find library -lxdo`.

| Subcommand | Crate | Role |
|---|---|---|
| `daemon` | `st-daemon` | watch repos, seal ledger, sync |
| `serve` | `st-server` | API + static dashboard |
| (CLI) | `st-helper` | apikey / start / stop / status |
| `tray` / `--tray` | `st-tray` | desktop tray or headless supervisor |
| (shared) | `st-core` | scoring, ledger, privacy |

Desktop double-click / no TTY / `--tray` starts the tray. Terminal commands
(`status`, `apikey`, …) run as a normal CLI (Windows release builds attach the
parent console).

```bash
cargo test --workspace
cargo build --release -p struggletime
# from repo root:
make install
```

Privacy: buckets are allowlisted metadata only. File identities are
`sha256(salt:path)` hex ids. Never raw paths, prompts, or contents.
