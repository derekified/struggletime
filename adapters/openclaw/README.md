# OpenClaw

OpenClaw runs agents inside its gateway process, not your shell, so
the shell hook alone sees the *results* (file writes in the workspace,
git commits — Layer 1 still counts those) but misses turns and tool
exit codes.

Run this next to the daemon and the gap closes:

```bash
python3 adapters/openclaw/adapter.py   # tails ~/.openclaw/agents/main/sessions
python3 daemon/daemon.py --repo ~/.openclaw/workspace/your-project --server http://127.0.0.1:18765 --user you
```

The adapter reads OpenClaw's local JSONL session logs and appends only
counts to the daemon exec log: user turn = prompt, tool call = agent
activity, tool result error = failed execution. It never copies message
text, tool arguments, or file contents — `classify()` looks at the
shape of each entry and throws the content away.

Honest limits: OpenClaw driving a *different* machine or container
writes nothing locally, so only git anchors survive; and a teen
steering purely by chat while the agent works will read as agent-heavy
until those turns flow through this adapter.
