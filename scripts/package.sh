#!/bin/sh
# Build portable StruggleTime source tarball/zip (binaries come from Releases / make rust).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER="${1:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || echo dev)}"
DIST="$ROOT/dist"
NAME="struggletime-${VER}"
STAGE="$DIST/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"

for d in rust daemon server adapters schemas weighting examples docs docs-site scripts; do
  if [ -d "$ROOT/$d" ]; then
    mkdir -p "$STAGE/$d"
    cp -a "$ROOT/$d/." "$STAGE/$d/"
  fi
done
for f in LICENSE README.md CONTRIBUTING.md Makefile SLACK.md .env.example struggletime.svg; do
  [ -f "$ROOT/$f" ] && cp "$ROOT/$f" "$STAGE/"
done
# strip build artifacts
rm -rf "$STAGE/rust/target" "$STAGE/docs-site/node_modules" "$STAGE/docs-site/.next" "$STAGE/docs-site/out" 2>/dev/null || true
rm -f "$STAGE/server/"*.db "$STAGE/.env" 2>/dev/null || true

cat > "$STAGE/INSTALL.txt" <<EOF
StruggleTime ${VER}
==================

Build native binaries:
  make rust && make install-binaries

Or download a release zip for your OS from:
  https://github.com/derekified/struggletime/releases

Then:
  struggletime apikey <st_…>
  struggletime start --repo ~/my-project
  struggletime serve

Requires: Rust toolchain (to build) or a prebuilt release zip; git.
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
