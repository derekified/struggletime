# StruggleTime Dashboard (Tauri)

A Tauri 2 desktop shell for the existing StruggleTime dashboard, with local tracker controls. The main window loads the configured dashboard directly; a separate bundled **Tracker Settings** window controls the existing `struggletime` CLI sidecar. The settings window also lists and unlinks linked projects (multi-project tracking) and lists / installs the optional editor & agent adapters via `struggletime adapters …`.

The dashboard needs internet access. The remote page does **not** receive native IPC access. Only the local settings window can invoke the fixed tracker commands.

## Prerequisites

- Node.js 22 and npm
- Stable Rust and Cargo
- **Windows:** Microsoft WebView2 Runtime (normally preinstalled on current Windows 10/11)
- **macOS:** Xcode command-line tools
- **Debian/Ubuntu:** GTK/WebKitGTK and tray build dependencies

```bash
sudo apt-get install -y \
  build-essential pkg-config libssl-dev \
  libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
  libxdo-dev libayatana-appindicator3-dev
```

## Development

From the repository root:

```bash
npm --prefix desktop ci
npm --prefix desktop run check
npm --prefix desktop run build
npm --prefix desktop run tauri:dev
```

`tauri:dev` first builds the existing Rust CLI as a host-targeted sidecar, then starts Vite and the Tauri application. The native menu opens **Tracker Settings**.

Useful overrides:

- `STRUGGLETIME_CLI`: use a specific existing CLI executable instead of sidecar discovery.
- `STRUGGLETIME_HOME`: isolate daemon state and event logs.
- `STRUGGLETIME_CONFIG`: isolate API keys and linked-project settings.
- `TAURI_TARGET` or `CARGO_BUILD_TARGET`: select the sidecar Rust target.

The dashboard origin comes from the same shared settings as the tray/CLI. A configured API key is injected only into that exact dashboard origin, never into OAuth-provider pages. Remote dashboard content cannot invoke tracker commands.

## Tests and checks

```bash
npm --prefix desktop run check
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
```

## Release bundle

Host build:

```bash
npm --prefix desktop run tauri:build
```

Explicit Rust target:

```bash
cd desktop
node scripts/prepare-sidecar.mjs --profile release --target x86_64-pc-windows-msvc
npx tauri build --target x86_64-pc-windows-msvc
```

Tauri writes platform installers under `desktop/src-tauri/target/<target>/release/bundle/`. Each packaged application includes the existing `struggletime` sidecar; daemon lifecycle, settings, and ledger behavior stay in that CLI.
