#!/bin/sh
# install the struggletime helper for this user (linux + macos). no sudo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"; mkdir -p "$BIN" "$HOME/.aitime"
ln -sf "$ROOT/daemon/helper.py" "$BIN/struggletime-helper"
chmod +x "$ROOT/daemon/helper.py"
case "$(uname -s)" in
  Linux)
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/struggletime.service" <<EOF
[Unit]
Description=StruggleTime helper
After=network.target
[Service]
ExecStart=/usr/bin/env python3 $ROOT/daemon/daemon.py --repo ${REPO:-$HOME} --user ${USER_NAME:-}
Restart=on-failure
[Install]
WantedBy=default.target
EOF
    echo "systemd user unit written. enable after editing the repo/user in it:"
    echo "  systemctl --user daemon-reload && systemctl --user enable --now struggletime"
    ;;
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.struggletime.helper.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.struggletime.helper</string>
<key>ProgramArguments</key><array><string>$BIN/struggletime-helper</string><string>start</string><string>--repo</string><string>${REPO:-$HOME}</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
EOF
    echo "launchd plist written: $PLIST (edit the repo, then: launchctl load $PLIST)"
    ;;
esac
echo "shell hook (add to your .bashrc/.zshrc):  source $ROOT/daemon/hooks.sh"
echo "run it now:  struggletime-helper start --repo ~/your-project --user you"
