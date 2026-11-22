#!/bin/sh
# Privacy + presence checks for adapters/ (no language runtime required).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

need() {
  if [ ! -e "$ROOT/$1" ]; then
    echo "MISSING $1" >&2
    fail=1
  else
    echo "ok  $1"
  fi
}

need adapters/vscode/extension.js
need adapters/vscode/package.json
need adapters/claude-code/hooks.json
need adapters/codex/wrap.sh
need adapters/neovim/lua/struggletime/init.lua
need adapters/godot/addons/struggletime/plugin.cfg
need adapters/jetbrains/src/main/resources/META-INF/plugin.xml
need adapters/unity/Editor/StruggleTimeTracker.cs
need adapters/openclaw/README.md
need daemon/hooks.sh
need daemon/hooks.ps1

# Forbidden content keys must not appear as emit templates in adapter sources
if grep -RInE '"(content|prompt_text|filepath|file_path|keystrokes|screenshot)"\s*:' \
  "$ROOT/adapters" "$ROOT/daemon/hooks.sh" "$ROOT/daemon/hooks.ps1" 2>/dev/null \
  | grep -v README | grep -v Binary; then
  echo "FAIL forbidden content-shaped keys in adapters" >&2
  fail=1
else
  echo "ok  no forbidden content keys in adapter emit templates"
fi

# Legacy names must stay out of tracked content (not the local checkout folder name)
if git -C "$ROOT" grep -nIE 'hackatime-next|Homebrew|\bHackatime\b' -- \
  ':!Cargo.lock' ':!docs-site/package-lock.json' 2>/dev/null; then
  echo "FAIL legacy brand strings found" >&2
  fail=1
else
  echo "ok  no legacy brand strings"
fi

exit "$fail"
