# StruggleTime Rust workspace

Single binary `struggletime` (crate `crates/struggletime`) bundles tray, CLI,
daemon, and local HTTP server.

```bash
cargo build --release -p struggletime
# Linux release packages with AppIndicator tray:
cargo build --release -p struggletime --features linux-tray
./target/release/struggletime --help
./target/release/struggletime --tray
```

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
