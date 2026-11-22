#!/bin/sh
# Build portable StruggleTime tarball/zip (no root). Linux/macOS/Windows via install scripts.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER="${1:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || echo dev)}"
DIST="$ROOT/dist"
NAME="struggletime-${VER}"
STAGE="$DIST/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# Core tree (no .git, no local db/env secrets)
for d in daemon src server adapters schemas weighting examples docs; do
  if [ -d "$ROOT/$d" ]; then
    mkdir -p "$STAGE/$d"
    cp -a "$ROOT/$d/." "$STAGE/$d/"
  fi
done
for f in LICENSE README.md CONTRIBUTING.md Makefile SLACK.md .env.example struggletime.svg; do
  [ -f "$ROOT/$f" ] && cp "$ROOT/$f" "$STAGE/"
done
if [ -f "$ROOT/daemon/assets/struggletime.svg" ]; then
  mkdir -p "$STAGE/daemon/assets"
  cp "$ROOT/daemon/assets/struggletime.svg" "$STAGE/daemon/assets/"
fi
if [ -f "$ROOT/server/static/img/struggletime.svg" ]; then
  mkdir -p "$STAGE/server/static/img"
  cp "$ROOT/server/static/img/struggletime.svg" "$STAGE/server/static/img/"
fi
# strip caches
find "$STAGE" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
rm -f "$STAGE/server/"*.db "$STAGE/.env" 2>/dev/null || true

cat > "$STAGE/INSTALL.txt" <<EOF
StruggleTime ${VER}
==================

Linux / macOS:
  sh daemon/install.sh

Windows (PowerShell):
  powershell -ExecutionPolicy Bypass -File daemon\\install.ps1

Then:
  struggletime-helper apikey <st_…>
  struggletime-helper start
  open the dashboard URL printed by install (or https://62-83-116-143.sslip.io)

Requires: Python 3.10+, git. Optional: cryptography (for faster Ed25519) or openssl.
EOF

mkdir -p "$DIST"
TAR="$DIST/${NAME}.tar.gz"
tar -C "$DIST" -czf "$TAR" "$NAME"
echo "built $TAR"
if command -v zip >/dev/null 2>&1; then
  ZIP="$DIST/${NAME}.zip"
  ( cd "$DIST" && zip -qr "$ZIP" "$NAME" )
  echo "built $ZIP"
  ls -lh "$TAR" "$ZIP"
else
  ls -lh "$TAR"
fi
rm -rf "$STAGE"
