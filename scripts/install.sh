#!/bin/sh
# Install StruggleTime as a desktop app + CLI (macOS / Linux).
# Works from a git checkout OR an extracted GitHub Release folder.
#
# Usage:
#   ./scripts/install.sh                          # source tree
#   ./install.sh ./struggletime                   # release archive
#   PREFIX=/usr/local ./scripts/install.sh …
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Repo checkout: …/scripts/install.sh → root is parent.
# Release archive: …/install.sh next to struggletime (+ optional StruggleTime.app).
if [ -d "$HERE/scripts" ] && [ -f "$HERE/scripts/package_macos_app.sh" ]; then
  ROOT="$HERE"
  PACKAGER="$HERE/scripts/package_macos_app.sh"
elif [ -f "$HERE/package_macos_app.sh" ]; then
  ROOT="$(cd "$HERE/.." && pwd)"
  PACKAGER="$HERE/package_macos_app.sh"
elif [ -f "$HERE/../scripts/package_macos_app.sh" ]; then
  ROOT="$(cd "$HERE/.." && pwd)"
  PACKAGER="$ROOT/scripts/package_macos_app.sh"
else
  ROOT="$HERE"
  PACKAGER=""
fi

PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
SHARE="$PREFIX/share"
APP_SUPPORT="$SHARE/applications"
ICON_DIR="$SHARE/icons/hicolor/scalable/apps"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
OS="$(uname -s)"

mkdir -p "$BIN_DIR"

resolve_binary() {
  if [ -n "${1:-}" ] && [ -x "$1" ]; then
    echo "$1"
    return
  fi
  for cand in \
    "$HERE/struggletime" \
    "$ROOT/rust/target/release/struggletime" \
    "$HERE/StruggleTime.app/Contents/MacOS/struggletime"
  do
    if [ -x "$cand" ]; then
      echo "$cand"
      return
    fi
  done
  if command -v struggletime >/dev/null 2>&1; then
    command -v struggletime
    return
  fi
  if [ -d "$ROOT/rust" ]; then
    echo "building release binary…" >&2
    ( cd "$ROOT/rust" && cargo build --release -p struggletime )
    echo "$ROOT/rust/target/release/struggletime"
    return
  fi
  echo "could not find a struggletime binary" >&2
  exit 1
}

SRC="$(resolve_binary "${1:-}")"
echo "installing from $SRC"

case "$OS" in
  Darwin)
    APP_DEST="/Applications/StruggleTime.app"
    if [ -d "$HERE/StruggleTime.app" ]; then
      APP_SRC="$HERE/StruggleTime.app"
      USE_EXISTING=1
    else
      USE_EXISTING=0
      TMP="$(mktemp -d)"
      if [ -n "$PACKAGER" ] && [ -f "$PACKAGER" ]; then
        sh "$PACKAGER" "$SRC" "$TMP"
      else
        echo "missing package_macos_app.sh; copy StruggleTime.app manually" >&2
        exit 1
      fi
      APP_SRC="$TMP/StruggleTime.app"
    fi
    if [ -w /Applications ] || mkdir -p /Applications 2>/dev/null; then
      rm -rf "$APP_DEST"
      cp -R "$APP_SRC" "$APP_DEST"
    else
      APP_DEST="$HOME/Applications/StruggleTime.app"
      mkdir -p "$HOME/Applications"
      rm -rf "$APP_DEST"
      cp -R "$APP_SRC" "$APP_DEST"
    fi
    [ "${USE_EXISTING:-0}" = "0" ] && rm -rf "${TMP:-}"
    ln -sf "$APP_DEST/Contents/MacOS/struggletime" "$BIN_DIR/struggletime"
    if [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
      ln -sf "$APP_DEST/Contents/MacOS/struggletime" /usr/local/bin/struggletime
      echo "CLI: /usr/local/bin/struggletime"
    fi
    echo "App: $APP_DEST"
    echo "CLI: $BIN_DIR/struggletime"
    echo "Open StruggleTime from Applications, or run: struggletime --tray"
    ;;
  Linux)
    install -m 755 "$SRC" "$BIN_DIR/struggletime"
    mkdir -p "$APP_SUPPORT" "$ICON_DIR"
    for icon in \
      "$HERE/share/icons/struggletime.svg" \
      "$ROOT/daemon/assets/struggletime.svg" \
      "$HERE/struggletime.svg"
    do
      if [ -f "$icon" ]; then
        install -m 644 "$icon" "$ICON_DIR/struggletime.svg"
        break
      fi
    done
    cat > "$APP_SUPPORT/struggletime.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=StruggleTime
Comment=Track struggle-weighted coding time
Exec=$BIN_DIR/struggletime --tray
Icon=struggletime
Terminal=false
Categories=Development;Utility;
StartupNotify=false
EOF
    if [ "${AUTOSTART_INSTALL:-1}" = "1" ]; then
      mkdir -p "$AUTOSTART"
      cp "$APP_SUPPORT/struggletime.desktop" "$AUTOSTART/struggletime.desktop"
      echo "autostart: $AUTOSTART/struggletime.desktop"
    fi
    echo "App launcher: $APP_SUPPORT/struggletime.desktop"
    echo "CLI: $BIN_DIR/struggletime"
    case ":$PATH:" in
      *":$BIN_DIR:"*) ;;
      *) echo "add $BIN_DIR to PATH (export PATH=\"$BIN_DIR:\$PATH\")" ;;
    esac
    echo "try: struggletime --version && struggletime status"
    ;;
  *)
    install -m 755 "$SRC" "$BIN_DIR/struggletime"
    echo "CLI: $BIN_DIR/struggletime"
    ;;
esac
