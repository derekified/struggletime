#!/bin/sh
# Install StruggleTime so the tray starts on login. No sudo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${HOME}/.local/bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/struggletime"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DATA="${STRUGGLETIME_HOME:-$HOME/.aitime}"

mkdir -p "$BIN" "$CONFIG" "$DATA"
sh "$ROOT/scripts/install_binaries.sh"

LAUNCHER="$BIN/struggletime-login"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
export PATH="\$HOME/.local/bin:\$PATH"
export STRUGGLETIME_CONFIG="\${STRUGGLETIME_CONFIG:-\$HOME/.config/struggletime}"
export AITIME_HOME="\${AITIME_HOME:-\$HOME/.aitime}"
mkdir -p "\$STRUGGLETIME_CONFIG" "\$AITIME_HOME"
exec struggletime-tray --headless
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
Exec=$LAUNCHER
X-GNOME-Autostart-enabled=true
EOF
    cat > "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/struggletime.service" <<EOF
[Unit]
Description=StruggleTime tray
[Service]
ExecStart=$LAUNCHER
Restart=on-failure
[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now struggletime.service 2>/dev/null || true
    echo "autostart: $AUTOSTART/struggletime.desktop"
    ;;
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.struggletime.tray.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.struggletime.tray</string>
  <key>ProgramArguments</key><array><string>$LAUNCHER</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" 2>/dev/null || true
    echo "autostart: $PLIST"
    ;;
  *)
    echo "unsupported OS: $OS — binaries are in $BIN"
    ;;
esac
