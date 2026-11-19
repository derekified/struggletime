# Codex / Aider / any CLI agent

No adapter needed: the shell hook already classifies `codex`, `aider`,
`claude` as `ai_cli` (name only). For exact turn boundaries, wrap:

```bash
codex() { echo '{"category":"ai_cli","verb":"start","exit_code":0}' >> ~/.aitime/exec.jsonl; command codex "$@"; echo "{\"category\":\"ai_cli\",\"verb\":\"stop\",\"exit_code\":$?}" >> ~/.aitime/exec.jsonl; }
```

Unattended turns are discounted by the state machine, not by the adapter.
