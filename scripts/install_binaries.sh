#!/bin/sh
# Install Rust StruggleTime binaries into ~/.local/bin (no Python required).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"

if [ ! -x "$ROOT/rust/target/release/struggletime-helper" ]; then
  echo "building release binaries…"
  ( cd "$ROOT/rust" && cargo build --release )
fi

for name in struggletime-helper struggletime-daemon struggletime-server struggletime-tray; do
  src="$ROOT/rust/target/release/$name"
  if [ -x "$src" ]; then
    ln -sf "$src" "$BIN/$name"
    echo "linked $BIN/$name"
  else
    echo "missing $src" >&2
    exit 1
  fi
done

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "add $BIN to PATH (e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
esac

echo "done. try: struggletime-helper status"
echo "server:   struggletime-server   # serves server/static from repo root"
