#!/bin/sh
# Install StruggleTime as a desktop app + CLI.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec sh "$ROOT/scripts/install.sh" "$@"
