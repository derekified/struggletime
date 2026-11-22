# StruggleTime Rust workspace

Single binary `struggletime` (crate `crates/struggletime`) bundles tray, CLI,
daemon, and local HTTP server.

```bash
cargo build --release -p struggletime
./target/release/struggletime --help
```

| Subcommand | Crate | Role |
|---|---|---|
| `daemon` | `st-daemon` | watch repos, seal ledger, sync |
| `serve` | `st-server` | API + static dashboard |
| (CLI) | `st-helper` | apikey / start / stop / status |
| `tray` | `st-tray` | headless supervisor |
| (shared) | `st-core` | scoring, ledger, privacy |

Legacy thin binaries (`struggletime-daemon`, …) still build from the same libs
for local debugging. Releases ship only `struggletime`.

```bash
cargo test --workspace
cargo build --release -p struggletime
# from repo root:
make install-binaries
```

Privacy: buckets are allowlisted metadata only. File identities are
`sha256(salt:path)` hex ids. Never raw paths, prompts, or contents.
