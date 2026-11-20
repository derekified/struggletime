#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Install StruggleTime so the tray (and linked daemon) start on login.
# Linux: ~/.config/autostart + systemd --user
# macOS: LaunchAgents
# Does not require sudo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${HOME}/.local/bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/struggletime"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DATA="${STRUGGLETIME_HOME:-$HOME/.aitime}"
PYTHON="${PYTHON:-python3}"

mkdir -p "$BIN" "$CONFIG" "$DATA"
ln -sf "$ROOT/daemon/tray.py" "$BIN/struggletime-tray"
ln -sf "$ROOT/daemon/helper.py" "$BIN/struggletime-helper"
ln -sf "$ROOT/daemon/gui.py" "$BIN/struggletime"
chmod +x "$ROOT/daemon/tray.py" "$ROOT/daemon/helper.py" "$ROOT/daemon/gui.py" "$ROOT/daemon/autostart.sh"

# Small launcher that loads settings from ~/.config/struggletime and starts the tray.
LAUNCHER="$BIN/struggletime-login"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
export PATH="\$HOME/.local/bin:\$PATH"
export STRUGGLETIME_CONFIG="\${STRUGGLETIME_CONFIG:-\$HOME/.config/struggletime}"
export AITIME_HOME="\${AITIME_HOME:-\$HOME/.aitime}"
mkdir -p "\$STRUGGLETIME_CONFIG" "\$AITIME_HOME"
exec $PYTHON "$ROOT/daemon/tray.py" "\$@"
EOF
chmod +x "$LAUNCHER"

OS="$(uname -s)"
case "$OS" in
  Linux)
    mkdir -p "$AUTOSTART" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    cat > "$AUTOSTART/struggletime.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=StruggleTime
Comment=Start StruggleTime tray tracker on login
Exec=$LAUNCHER
Icon=utilities-terminal
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
Categories=Utility;
EOF
    cat > "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/struggletime-tray.service" <<EOF
[Unit]
Description=StruggleTime tray / daemon
After=graphical-session.target network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=STRUGGLETIME_CONFIG=%h/.config/struggletime
Environment=AITIME_HOME=%h/.aitime
ExecStart=$LAUNCHER
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user daemon-reload 2>/dev/null || true
      systemctl --user enable --now struggletime-tray.service 2>/dev/null || true
    fi
    echo "autostart desktop: $AUTOSTART/struggletime.desktop"
    echo "systemd user unit: struggletime-tray.service (enable --now if needed)"
    ;;
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.struggletime.tray.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.struggletime.tray</string>
  <key>ProgramArguments</key>
  <array>
    <string>$LAUNCHER</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>STRUGGLETIME_CONFIG</key><string>$HOME/.config/struggletime</string>
    <key>AITIME_HOME</key><string>$HOME/.aitime</string>
  </dict>
  <key>StandardOutPath</key><string>$DATA/tray.out.log</string>
  <key>StandardErrorPath</key><string>$DATA/tray.err.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" 2>/dev/null || true
    echo "launchd plist: $PLIST"
    ;;
  *)
    echo "unsupported OS: $OS — launcher installed at $LAUNCHER"
    ;;
esac

echo
echo "settings dir:  $CONFIG/settings.json"
echo "runtime dir:   $DATA"
echo "login launcher: $LAUNCHER"
echo "start now:      $LAUNCHER"
echo "or:             $BIN/struggletime-tray"
