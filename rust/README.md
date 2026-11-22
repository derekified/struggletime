# StruggleTime Rust workspace

Native rewrite of the desktop tracker, HTTP server, helper CLI, and tray
supervisor. Users can run StruggleTime without installing Python.

| Binary | Crate | Role |
|---|---|---|
| `struggletime-daemon` | `st-daemon` | watch repos, seal ledger, sync |
| `struggletime-server` | `st-server` | API + static dashboard |
| `struggletime-helper` | `st-helper` | apikey / start / stop / status |
| `struggletime-tray` | `st-tray` | headless supervisor (release tray UI later) |

Shared scoring and config: `st-core` (port of `src/verify.py` + settings paths).

```bash
cargo test
cargo build --release
# from repo root:
make install-binaries
```

Privacy: buckets are allowlisted metadata only; file identities are
`sha256(salt:path)` hex ids — never raw paths, prompts, or contents.
