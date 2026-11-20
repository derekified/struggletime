#!/bin/sh
# install the struggletime helper + tray, fix server URL to sslip.io, and start both.
# linux + macos. no sudo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/struggletime"
DATA="${STRUGGLETIME_HOME:-$HOME/.aitime}"
PYTHON="${PYTHON:-python3}"
PUBLIC_SERVER="${STRUGGLETIME_SERVER:-${AITIME_SERVER:-https://62-83-116-143.sslip.io}}"

mkdir -p "$BIN" "$CONFIG" "$DATA"

ln -sf "$ROOT/daemon/helper.py" "$BIN/struggletime-helper"
ln -sf "$ROOT/daemon/gui.py" "$BIN/struggletime"
ln -sf "$ROOT/daemon/tray.py" "$BIN/struggletime-tray"
chmod +x "$ROOT/daemon/helper.py" "$ROOT/daemon/gui.py" "$ROOT/daemon/tray.py" \
  "$ROOT/daemon/autostart.sh" "$ROOT/daemon/install.sh"

# Migrate legacy ~/.aitime/gui.json → ~/.config/struggletime/settings.json
if [ -f "$DATA/gui.json" ] && [ ! -f "$CONFIG/settings.json" ]; then
  cp "$DATA/gui.json" "$CONFIG/settings.json"
  echo "migrated settings → $CONFIG/settings.json"
fi

# Rewrite dead Cloudflare / ngrok tunnel URLs to the stable public host
export STRUGGLETIME_CONFIG="$CONFIG"
export AITIME_HOME="$DATA"
export STRUGGLETIME_SERVER="$PUBLIC_SERVER"
export AITIME_SERVER="$PUBLIC_SERVER"
SERVER_NOW="$(
  ROOT="$ROOT" "$PYTHON" - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["ROOT"]) / "daemon"))
import config as dcfg
print(dcfg.ensure_public_server())
PY
)"
echo "server → $SERVER_NOW"

# Wire tray to start on login (desktop entry / systemd --user / LaunchAgents)
"$ROOT/daemon/autostart.sh"

# Start tray now (systemd user unit when available, else background)
started_tray=0
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload 2>/dev/null || true
  if systemctl --user enable --now struggletime-tray.service 2>/dev/null; then
    started_tray=1
    echo "tray: systemd --user struggletime-tray.service started"
  fi
fi
if [ "$started_tray" -eq 0 ]; then
  if ! pgrep -f "[d]aemon/tray.py" >/dev/null 2>&1; then
    nohup "$PYTHON" "$ROOT/daemon/tray.py" >>"$DATA/tray.out.log" 2>>"$DATA/tray.err.log" &
    echo "tray: started pid $! (logs in $DATA/tray.*.log)"
  else
    echo "tray: already running"
  fi
fi

# Start helper/daemon if settings already have repo + API key
if "$BIN/struggletime-helper" status 2>/dev/null | grep -q '^running'; then
  echo "helper: daemon already running"
elif "$PYTHON" - <<PY
import json, sys
from pathlib import Path
p = Path("$CONFIG/settings.json")
if not p.exists():
    sys.exit(1)
s = json.loads(p.read_text())
sys.exit(0 if (s.get("repo") and s.get("token")) else 1)
PY
then
  if "$BIN/struggletime-helper" start; then
    echo "helper: tracker started from settings.json"
  else
    echo "helper: could not auto-start (check: struggletime-helper status)"
  fi
else
  echo "helper: set an API key, then start tracking:"
  echo "  struggletime-helper apikey <st_… key from Settings>"
  echo "  struggletime-helper start --repo ~/your-project"
fi

echo
echo "shell hook (add to your .bashrc/.zshrc):  source $ROOT/daemon/hooks.sh"
echo "tray GUI:   struggletime-tray"
echo "tray CLI:   struggletime-helper   (same controls as the tray)"
echo "settings:   $CONFIG/settings.json"
echo "server:     $SERVER_NOW"
