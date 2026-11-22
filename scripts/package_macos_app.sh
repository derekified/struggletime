#!/bin/sh
# Build StruggleTime.app from a release binary.
# Usage: package_macos_app.sh <struggletime-binary> <out-dir>
set -euo pipefail

BIN="${1:?path to struggletime binary}"
OUT="${2:?output directory}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$OUT/StruggleTime.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"
# Prefer workspace Cargo.toml version; override with STRUGGLETIME_VERSION.
APP_VERSION="${STRUGGLETIME_VERSION:-}"
if [ -z "$APP_VERSION" ] && [ -f "$ROOT/rust/Cargo.toml" ]; then
  APP_VERSION="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$ROOT/rust/Cargo.toml" | head -1)"
fi
APP_VERSION="${APP_VERSION:-0.1.3}"

rm -rf "$APP"
mkdir -p "$MACOS" "$RES"
cp "$BIN" "$MACOS/struggletime"
chmod +x "$MACOS/struggletime"

# Finder / Launchpad entry: always start tray (no Dock icon via LSUIElement).
cat > "$MACOS/StruggleTime" <<'WRAP'
#!/bin/sh
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/struggletime" --tray
WRAP
chmod +x "$MACOS/StruggleTime"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>StruggleTime</string>
  <key>CFBundleIdentifier</key>
  <string>com.struggletime.app</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>StruggleTime</string>
  <key>CFBundleDisplayName</key>
  <string>StruggleTime</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${APP_VERSION}</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

ICON_SRC="$ROOT/daemon/assets/struggletime.svg"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$RES/AppIcon.svg"
fi

echo "built $APP"
