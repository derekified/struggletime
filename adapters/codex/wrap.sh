#!/bin/sh
# Wrap a CLI agent to emit start/stop counts into ~/.aitime/agent_events.jsonl
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENT="${1:-codex}"
shift || true
OUT="${AITIME_AGENT_EVENTS:-$HOME/.aitime/agent_events.jsonl}"
mkdir -p "$(dirname "$OUT")"
ts() { date +%s; }
emit() {
  printf '{"ts":%s,"event_type":"%s","category":"ai_cli","editor":"%s","agent":"%s","files_touched_count":%s,"exit_code":%s,"source":"adapter"}\n' \
    "$(ts)" "$1" "$AGENT" "$AGENT" "${2:-0}" "${3:-0}" >> "$OUT"
}
emit start 0 0
set +e
"$AGENT" "$@"
code=$?
set -e
emit stop 1 "$code"
exit "$code"
