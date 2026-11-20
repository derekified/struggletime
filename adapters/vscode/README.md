# VS Code family adapter (Cursor / Windsurf / Trae / VSCodium / Code)

Optional enrichment only. Layer 1 already attributes these editors via the
stdlib process registry (`daemon/editors.py` + `~/.aitime/editors.json`).

This adapter appends allowlisted lines to `~/.aitime/agent_events.jsonl`
(counts + editor/agent labels — never document text or paths).

Works in any VS Code fork that exposes `vscode.env.appName`:

| appName contains | `editor` label |
|---|---|
| Cursor | `cursor` |
| Windsurf | `windsurf` |
| Trae | `trae` |
| Kiro | `kiro` |
| VSCodium / Code - OSS | `vscodium` |
| Visual Studio Code | `vscode` |

Load `extension.js` as a local extension. Any hook/wrapper that can write a
JSON line to `agent_events.jsonl` is a valid adapter — there is no MCP server.
