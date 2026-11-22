#!/bin/sh
# Stage a release folder with binary + install helpers for one OS artifact.
# Usage: stage_release.sh <artifact-name> <binary-path> <out-dist-dir>
set -euo pipefail

ARTIFACT="${1:?artifact name e.g. struggletime-linux-x86_64}"
BIN="${2:?binary path}"
DIST="${3:?dist dir}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DIST/$ARTIFACT"

rm -rf "$OUT"
mkdir -p "$OUT"

case "$ARTIFACT" in
  *windows*)
    cp "$BIN" "$OUT/struggletime.exe"
    cp "$ROOT/scripts/install.ps1" "$OUT/install.ps1"
    if [ -d "$ROOT/adapters" ]; then
      cp -a "$ROOT/adapters" "$OUT/adapters"
    fi
    if [ -d "$ROOT/daemon" ]; then
      mkdir -p "$OUT/daemon"
      cp -f "$ROOT/daemon/hooks.sh" "$ROOT/daemon/hooks.ps1" "$OUT/daemon/" 2>/dev/null || true
    fi
    cat > "$OUT/README.txt" <<'EOF'
StruggleTime for Windows
========================

1. Right-click install.ps1 → Run with PowerShell
   (or: powershell -ExecutionPolicy Bypass -File .\install.ps1)

That installs:
  %LocalAppData%\Programs\StruggleTime\struggletime.exe
  Start Menu → StruggleTime  (tray app)
  user PATH entry so `struggletime` works in new terminals

Optional login autostart:
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Autostart

Then:
  struggletime --version
  struggletime apikey <st_…>
  struggletime status
EOF
    ;;
  *macos*)
    cp "$BIN" "$OUT/struggletime"
    chmod +x "$OUT/struggletime"
    sh "$ROOT/scripts/package_macos_app.sh" "$OUT/struggletime" "$OUT"
    cp "$ROOT/scripts/install.sh" "$OUT/install.sh"
    chmod +x "$OUT/install.sh"
    if [ -d "$ROOT/adapters" ]; then
      cp -a "$ROOT/adapters" "$OUT/adapters"
      mkdir -p "$OUT/StruggleTime.app/Contents/Resources"
      cp -a "$ROOT/adapters" "$OUT/StruggleTime.app/Contents/Resources/adapters"
    fi
    if [ -d "$ROOT/daemon" ]; then
      mkdir -p "$OUT/daemon"
      cp -f "$ROOT/daemon/hooks.sh" "$ROOT/daemon/hooks.ps1" "$OUT/daemon/" 2>/dev/null || true
    fi
    cat > "$OUT/README.txt" <<'EOF'
StruggleTime for macOS
======================

Option A — App + CLI installer:
  sh ./install.sh ./struggletime

Copies StruggleTime.app to /Applications (or ~/Applications) and symlinks
`struggletime` into ~/.local/bin (and /usr/local/bin when writable).

Option B — manual:
  open StruggleTime.app          # menu-bar tray (no Dock icon)
  sudo ln -sf "$(pwd)/StruggleTime.app/Contents/MacOS/struggletime" /usr/local/bin/struggletime

Then:
  struggletime --version
  struggletime apikey <st_…>
  struggletime status
EOF
    ;;
  *linux*)
    cp "$BIN" "$OUT/struggletime"
    chmod +x "$OUT/struggletime"
    cp "$ROOT/scripts/install.sh" "$OUT/install.sh"
    chmod +x "$OUT/install.sh"
    mkdir -p "$OUT/share/icons"
    if [ -f "$ROOT/daemon/assets/struggletime.svg" ]; then
      cp "$ROOT/daemon/assets/struggletime.svg" "$OUT/share/icons/struggletime.svg"
    fi
    if [ -d "$ROOT/adapters" ]; then
      cp -a "$ROOT/adapters" "$OUT/adapters"
    fi
    if [ -d "$ROOT/daemon" ]; then
      mkdir -p "$OUT/daemon"
      cp -f "$ROOT/daemon/hooks.sh" "$ROOT/daemon/hooks.ps1" "$OUT/daemon/" 2>/dev/null || true
    fi
    cat > "$OUT/struggletime.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=StruggleTime
Comment=Track struggle-weighted coding time
Exec=struggletime --tray
Icon=struggletime
Terminal=false
Categories=Development;Utility;
StartupNotify=false
EOF
    cat > "$OUT/README.txt" <<'EOF'
StruggleTime for Linux
======================

  sh ./install.sh ./struggletime

Installs:
  ~/.local/bin/struggletime
  ~/.local/share/applications/struggletime.desktop
  optional ~/.config/autostart/struggletime.desktop

Release builds include the AppIndicator tray. Install libayatana-appindicator3
(or libappindicator) for the icon. Without it, StruggleTime still runs a
headless supervisor.

Then:
  struggletime --version
  struggletime apikey <st_…>
  struggletime status
EOF
    ;;
  *)
    cp "$BIN" "$OUT/struggletime"
    chmod +x "$OUT/struggletime" 2>/dev/null || true
    ;;
esac

echo "staged $OUT"
