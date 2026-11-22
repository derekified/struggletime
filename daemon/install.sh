#!/bin/sh
# Install StruggleTime native binaries (no system language runtime required beyond the binaries).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec sh "$ROOT/scripts/install_binaries.sh"
