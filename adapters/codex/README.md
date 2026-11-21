# Codex / Aider / any CLI agent

Layer 1 already classifies `codex` / `aider` / `claude` process names as
`ai_cli`. This adapter only enriches turn boundaries.

## Install (one line)

```bash
# from repo root
chmod +x adapters/codex/wrap.sh
alias codex='$PWD/adapters/codex/wrap.sh codex'
alias aider='$PWD/adapters/codex/wrap.sh aider'
```

Or copy the wrapper into `~/.local/bin/codex-st` and point your shell alias at it.

## What it writes

Allowlisted metadata only via `daemon/agent_event.py` →
`~/.aitime/agent_events.jsonl` (`start` / `stop`, exit code, editor/agent labels).
No prompts, no args, no file contents.

## Manual bridge

```bash
python3 daemon/agent_event.py --editor codex --agent codex --event-type start
codex "$@"
python3 daemon/agent_event.py --editor codex --agent codex --event-type stop \
  --op modified --exit-code $? --files-touched-count 1
```

Unattended dumps are discounted by the causal state machine, not by this adapter.
