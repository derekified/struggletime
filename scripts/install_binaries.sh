#!/bin/sh
# Back-compat entry: install App + CLI via scripts/install.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec sh "$ROOT/scripts/install.sh" "$@"
