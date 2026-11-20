# Codex / Aider / any CLI agent

No adapter needed: the shell hook already classifies `codex`, `aider`,
`claude` as `ai_cli` (name only). For exact turn boundaries + editor/agent
labels (used by Layer 1 file attribution), wrap:

```bash
codex() {
  echo '{"category":"ai_cli","verb":"start","editor":"codex","agent":"codex","exit_code":0}' >> ~/.aitime/exec.jsonl
  command codex "$@"
  echo "{\"category\":\"ai_cli\",\"verb\":\"stop\",\"editor\":\"codex\",\"agent\":\"codex\",\"op\":\"modified\",\"exit_code\":$?}" >> ~/.aitime/exec.jsonl
}
```

Unattended turns are discounted by the state machine, not by the adapter.
