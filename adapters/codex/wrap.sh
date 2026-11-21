#!/bin/sh
# Thin Codex/Aider wrapper → ~/.aitime/agent_events.jsonl (Layer-2 enrichment).
# Usage: alias codex='/path/to/adapters/codex/wrap.sh codex'
#        alias aider='/path/to/adapters/codex/wrap.sh aider'
set -e
AGENT="${1:-codex}"
shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRIDGE="$ROOT/daemon/agent_event.py"
PYTHON="${PYTHON:-python3}"
EVENTS="${AITIME_AGENT_EVENTS:-$HOME/.aitime/agent_events.jsonl}"
mkdir -p "$(dirname "$EVENTS")"

"$PYTHON" "$BRIDGE" --editor "$AGENT" --agent "$AGENT" --event-type start --category ai_cli 2>/dev/null || true
set +e
command "$AGENT" "$@"
ec=$?
set -e
"$PYTHON" "$BRIDGE" --editor "$AGENT" --agent "$AGENT" --event-type stop --category ai_cli \
  --op modified --exit-code "$ec" --files-touched-count 1 2>/dev/null || true
exit "$ec"
