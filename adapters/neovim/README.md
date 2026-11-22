# Neovim adapter

Optional Layer-2 enrichment. Layer 1 already labels `neovim` / `hx` via
`daemon/editors.py` process signatures.

This Lua plugin appends allowlisted focus / human-edit / external-write /
save counts to `~/.aitime/agent_events.jsonl`. No buffer text, paths, or
prompts leave the editor.

## Install

```lua
-- lazy.nvim example: point at this repo path
{
  dir = "/path/to/struggletime/adapters/neovim",
  name = "struggletime",
  config = function()
    require("struggletime").setup()
  end,
}
```

Or add `adapters/neovim` to `runtimepath` and call `require("struggletime").setup()`.

Override the bridge path with `AITIME_AGENT_EVENTS` if needed.
