#!/bin/sh
# install the struggletime helper + login autostart for this user (linux + macos). no sudo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/struggletime"
DATA="${STRUGGLETIME_HOME:-$HOME/.aitime}"
mkdir -p "$BIN" "$CONFIG" "$DATA"

ln -sf "$ROOT/daemon/helper.py" "$BIN/struggletime-helper"
ln -sf "$ROOT/daemon/gui.py" "$BIN/struggletime"
ln -sf "$ROOT/daemon/tray.py" "$BIN/struggletime-tray"
chmod +x "$ROOT/daemon/helper.py" "$ROOT/daemon/gui.py" "$ROOT/daemon/tray.py" "$ROOT/daemon/autostart.sh"

# Migrate legacy ~/.aitime/gui.json → ~/.config/struggletime/settings.json
if [ -f "$DATA/gui.json" ] && [ ! -f "$CONFIG/settings.json" ]; then
  cp "$DATA/gui.json" "$CONFIG/settings.json"
  echo "migrated settings → $CONFIG/settings.json"
fi

# Wire tray / daemon to start on login
"$ROOT/daemon/autostart.sh"

echo "shell hook (add to your .bashrc/.zshrc):  source $ROOT/daemon/hooks.sh"
echo "gui:  struggletime   (or: python3 $ROOT/daemon/gui.py)"
echo "tray: struggletime-tray"
echo "settings: $CONFIG/settings.json"
echo "run daemon now:  struggletime-helper start --repo ~/your-project --user you"
