#!/bin/sh
# Install the unified StruggleTime binary into ~/.local/bin.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"

SRC="$ROOT/rust/target/release/struggletime"
if [ ! -x "$SRC" ]; then
  echo "building release binary…"
  ( cd "$ROOT/rust" && cargo build --release -p struggletime )
fi

ln -sf "$SRC" "$BIN/struggletime"
echo "linked $BIN/struggletime"

# Optional legacy names → same binary (compat wrappers)
for alias in struggletime-helper struggletime-daemon struggletime-server struggletime-tray; do
  # thin shell wrappers that remap to subcommands
  case "$alias" in
    struggletime-helper)  cat > "$BIN/$alias" <<'EOF'
#!/bin/sh
exec "$(dirname "$0")/struggletime" "$@"
EOF
      ;;
    struggletime-daemon) cat > "$BIN/$alias" <<'EOF'
#!/bin/sh
exec "$(dirname "$0")/struggletime" daemon "$@"
EOF
      ;;
    struggletime-server) cat > "$BIN/$alias" <<'EOF'
#!/bin/sh
exec "$(dirname "$0")/struggletime" serve "$@"
EOF
      ;;
    struggletime-tray)   cat > "$BIN/$alias" <<'EOF'
#!/bin/sh
exec "$(dirname "$0")/struggletime" tray "$@"
EOF
      ;;
  esac
  chmod +x "$BIN/$alias"
  echo "wrapper $BIN/$alias"
done

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "add $BIN to PATH (e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
esac

echo "done. try: struggletime status"
echo "server:   struggletime serve"
