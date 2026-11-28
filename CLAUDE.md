# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run commands from the repository root unless a command changes directory. Make targets and repository test scripts require a POSIX shell; on Windows, use Git Bash or an equivalent environment for those targets. Cargo and npm commands also work directly in PowerShell.

### Rust build, test, and lint

```bash
make rust                 # Release desktop/CLI binary: rust/target/release/struggletime
make rust-linux           # Also enable the Linux AppIndicator tray
make rust-test            # cargo test --workspace (matches the CI test scope)
make check                # Default-member tests + adapter E2E + release-dependent adversarial harness
make clippy               # Clippy for all workspace targets; warnings are errors
make adversarial          # Isolated multi-project/privacy/adversarial integration harness

cd rust
cargo fmt --all -- --check
cargo test -p st-core
cargo test -p st-core chain_hashes_link -- --nocapture  # One test by name filter
cargo run -p struggletime -- status                     # Run the CLI without installing it
```

`make check` is the documented pre-PR check. CI separately checks formatting, Clippy, all workspace tests, default and `linux-tray` release builds, the adapter suite, and the docs build. `scripts/verify_adapters.sh` builds release desktop/server binaries when needed and exercises local ledger sealing and HTTP ingest; its shell harnesses require `python3` and `curl`.

Linux tray builds require GTK3, libxdo, Ayatana AppIndicator, and pkg-config. The exact Debian/Fedora/Arch package names are in `README.md` and `rust/README.md`.

### Run locally

```bash
make daemon REPO=/path/to/project   # Foreground tracker; set REPO explicitly
make tray                           # Build and launch the tray
make server                         # Build/run st-server on 127.0.0.1:18765
make docs-dev                       # Fumadocs development server
make docs                           # Static export under docs-site/out
make install                        # macOS/Linux desktop app + CLI installation
```

Server configuration uses `PORT`, `BIND`, `SERVER_DB`, and `STATIC_DIR`. Run the server from the repository root, or set `STATIC_DIR=server/static`, so it can find the dashboard.

### Documentation site

CI uses Node.js 22.

```bash
cd docs-site
npm ci
npm run build
npm run types:check
npm run dev
```

For `docs-site/` work, follow `docs-site/AGENTS.md`: consult the linked Fumadocs documentation for the relevant page, organization, UI, theme, or upgrade task rather than relying on older Fumadocs conventions.

### Desktop app (`desktop/`)

```bash
npm --prefix desktop ci
npm --prefix desktop run check
npm --prefix desktop run build
npm --prefix desktop run tauri:dev
npm --prefix desktop run tauri:build
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
```

- **Never search, glob, or read inside `target/`, `node_modules/`, `dist/`, or `desktop/src-tauri/binaries/`.** Scanning build artifacts floods the context window and causes upstream API timeouts.
- Keep responses and reasoning concise; execute one focused step at a time.
- On Windows, any background helper (`tasklist`, `taskkill`, etc.) spawned from GUI/tray code must set `CREATE_NO_WINDOW` (`0x0800_0000`) so no console window flashes.

## Architecture

### Binaries and crate boundaries

- `rust/crates/struggletime/src/main.rs` is the unified desktop executable. It dispatches to `st-daemon`, `st-helper`, and `st-tray`; it deliberately does **not** embed the HTTP server.
- `st-core` owns shared settings, adapter-event classification, privacy checks, scoring, canonical bucket hashing, chain verification, and the local ledger. Rust runtime scoring lives in `st-core/src/verify.rs`.
- `st-daemon` polls linked repositories, consumes adapter events, seals buckets, and uploads pending work.
- `st-helper` implements API-key/settings/project-link commands and starts, stops, and locates the daemon. `st-helper/src/adapters.rs` also discovers and installs adapters.
- `st-tray` presents the native tray where supported and otherwise runs a headless supervisor. Both restart the daemon only when valid watch targets are configured.
- `st-server` is the separate Axum/SQLite API and static-file server. Its SQLite schema is initialized in `st-server/src/lib.rs`.

The documented build and deployment path is Rust. A separate stdlib-Python server (`server/server.py`) and verifier (`src/verify.py`) remain in the repository and have their own feature surface. Do not assume endpoint, schema, or behavior parity between the Python and Rust servers; use the target implementation's router and tests as authority. The Python server imports its scoring from `src/verify.py`, so formula work may need coordinated changes in both implementations.

### Capture-to-score data flow

1. `st-daemon` scans each linked repository about once per second using file metadata (mtime and length), not source contents. Change bursts feed human/AI write heuristics.
2. Optional entries under `adapters/` and `daemon/hooks.*` append metadata-only JSONL events to the runtime directory. `st-core/agent_events.rs` recursively rejects forbidden keys, deduplicates identical lines, converts events to counts, and caps activity credit per drain so event fan-out cannot inflate active time.
3. Active slices are stamped with `FORMULA_VERSION`, linked with `prev_hash`, hashed, and appended to `<repo>/.aitime-ledger.json`. The ledger keeps both the complete `entries` chain and a `pending` upload queue.
4. Successful `POST /v1/buckets/batch` responses acknowledge pending hashes. Missing credentials, network failures, and rejected uploads leave entries local for a later ordered flush.
5. `st-server` authenticates bearer tokens by hash, rejects allowlist violations, logs chain-verification failures as warnings, calculates weighted seconds through `st-core`, and stores buckets idempotently by bucket hash.
6. The no-build SPA in `server/static/` is served by `st-server`. The Next.js/Fumadocs site in `docs-site/` is separately exported and served under `/docs` when its output has been built.

### Scoring and wire-format mirrors

`weighting/rules-v0.4.0.yaml` explicitly describes itself as a mirror of `st-core`, not the runtime engine. `schemas/heartbeat.schema.json` and the Python verifier are additional protocol/reference surfaces. Keep implementations, mirrors, fixtures, and `FORMULA_VERSION` consistent when changing scoring or bucket shape; do not edit only a YAML/JSON mirror and expect runtime behavior to change.

## Project-specific invariants

- **Privacy is a hard boundary.** Raw paths, source/text, prompts, command arguments, transcripts, keystrokes, screenshots, and pixels must never reach the server, including through tests and logs. Adapters contribute labels and counts only. File identities are salted SHA-256 IDs truncated to 16 hex characters.
- **Trust-path changes need regression tests.** Chain verification, allowlist validation, and weighting-state changes should include a failing-then-passing Rust test, as required by `CONTRIBUTING.md`.
- **Formula changes are versioned.** A scoring change requires a new `formula_version` and calibration notes under `weighting/`; do not silently retune an existing version.
- Keep the desktop stack in Rust. Optional adapters may use their host editor's language but must retain the metadata-only emit contract. Adapter installation/registry logic and `scripts/verify_adapters.sh` are part of the adapter integration surface.

## Local state and process behavior

- Runtime state defaults to `~/.aitime/` (event JSONL, daemon PID/log, and pause flag). Set `STRUGGLETIME_HOME` to isolate it.
- Settings default to the platform config directory (`%APPDATA%\struggletime` on Windows). Set `STRUGGLETIME_CONFIG` to isolate settings and credentials.
- Test harnesses use temporary runtime/config/home directories and dummy local credentials. Use the same overrides for manual daemon tests rather than touching a real tracking configuration.
- In an interactive terminal, bare `struggletime` prints status and exits, while `struggletime --tray` normally detaches and returns to the prompt. GUI/no-TTY launch selects the tray. Use `struggletime tray --headless` when a foreground supervisor is intended.
- `STRUGGLETIME_DAEMON`, `STRUGGLETIME_HELPER`, and `STRUGGLETIME_ADAPTERS` override executable/adapter discovery during development.
