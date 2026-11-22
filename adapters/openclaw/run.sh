#!/bin/sh
# OpenClaw session tail → ~/.aitime/agent_events.jsonl (counts only; never message text).
set -e
SESSIONS="${OPENCLAW_SESSIONS:-$HOME/.openclaw/agents/main/sessions}"
OUT="${AITIME_AGENT_EVENTS:-$HOME/.aitime/agent_events.jsonl}"
mkdir -p "$(dirname "$OUT")"
STATE="${STRUGGLETIME_HOME:-$HOME/.aitime}/openclaw.offset"
mkdir -p "$(dirname "$STATE")"
offset=0
[ -f "$STATE" ] && offset=$(cat "$STATE")

ONCE=0
[ "${1:-}" = "--once" ] && ONCE=1

emit() {
  # $1 event_type
  ts=$(date +%s)
  printf '{"ts":%s,"event_type":"%s","category":"ai_cli","editor":"openclaw","agent":"openclaw","files_touched_count":0,"exit_code":0,"source":"adapter"}\n' \
    "$ts" "$1" >> "$OUT"
}

scan() {
  [ -d "$SESSIONS" ] || return 0
  # shellcheck disable=SC2044
  for f in $(find "$SESSIONS" -type f -name '*.jsonl' 2>/dev/null | sort); do
    sz=$(wc -c < "$f" | tr -d ' ')
    # Heuristic: new bytes since last global offset file → count tool/user turns by line shape keys only
    while IFS= read -r line; do
      case "$line" in
        *'"type":"user"'*|*'\"role\":\"user\"'*) emit prompt ;;
        *'"type":"tool"'*|*'\"tool_call\"'*) emit tool ;;
        *'"type":"error"'*) emit exec ;;
      esac
    done < "$f"
    echo "$sz" > "$STATE"
  done
}

if [ "$ONCE" = 1 ]; then
  scan
  exit 0
fi
while true; do
  scan
  sleep 2
done
