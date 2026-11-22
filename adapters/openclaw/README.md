# OpenClaw

OpenClaw runs agents inside its gateway, so shell hooks see file/git results
but miss turns. Run this next to the daemon:

```bash
sh adapters/openclaw/run.sh          # tails local session JSONL
struggletime start --repo ~/.openclaw/workspace/your-project
```

`--once` drains once and exits (for checks). The script appends allowlisted
counts only to `~/.aitime/agent_events.jsonl`. never message text, tool
arguments, or file contents.
