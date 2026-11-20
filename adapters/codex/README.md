# Codex / Aider / any CLI agent

No MCP server. Prefer the unified agent-event bridge:

```bash
# start of a turn
python3 daemon/agent_event.py --editor codex --agent codex --event-type start
# or wrap the binary:
codex() {
  python3 "$(dirname "$0")/../../daemon/agent_event.py" --editor codex --agent codex --event-type start
  command codex "$@"
  local ec=$?
  python3 "$(dirname "$0")/../../daemon/agent_event.py" --editor codex --agent codex --event-type stop --op modified --exit-code "$ec" --files-touched-count 1
  return $ec
}
```

Shell-only fallback (still allowlisted metadata):

```bash
codex() {
  echo '{"event_type":"start","category":"ai_cli","verb":"start","editor":"codex","agent":"codex","exit_code":0,"files_touched_count":0}' >> ~/.aitime/agent_events.jsonl
  command codex "$@"
  echo "{\"event_type\":\"stop\",\"category\":\"ai_cli\",\"verb\":\"stop\",\"editor\":\"codex\",\"agent\":\"codex\",\"op\":\"modified\",\"exit_code\":$?,\"files_touched_count\":1}" >> ~/.aitime/agent_events.jsonl
}
```

Layer 1 also classifies `codex` / `aider` / `claude` process names as `ai_cli` if no adapter event arrives. Unattended dumps are discounted by the state machine, not by the adapter.
