#!/bin/sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v cargo >/dev/null || { echo "cargo required"; exit 1; }
command -v git >/dev/null || { echo "git required"; exit 1; }
echo "ok  cargo + git"
( cd "$ROOT/rust" && cargo test -q )
echo "ok  rust tests"
sh "$ROOT/scripts/verify_adapters.sh"
echo "ok  all checks"
