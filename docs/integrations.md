# Integrations

Live docs (setup for every editor/agent adapter, shell hooks, GitHub,
Slack): `/docs/integrations` — source
`docs-site/content/docs/integrations.mdx`.

## Layer 1 (no plugin)

Process labels from `daemon/editors.py` — AI IDEs, editors, JetBrains,
Godot/Unity/KiCad/Blender/CAD/creative tools, Jupyter/RStudio/Obsidian,
and CLI agents (Claude Code, Codex, OpenClaw, Aider, Goose, OpenCode,
Gemini, Amp, Crush, Qwen, Cline, Roo, Continue, Copilot, …). Full tables
on `/docs/daemon`.

## Layer 2 adapters under `adapters/`

| Path | Tools |
|---|---|
| `vscode/` | VS Code, Cursor, Windsurf, Trae, Kiro, VSCodium |
| `neovim/` | Neovim |
| `jetbrains/` | IDEA, PyCharm, CLion, WebStorm, GoLand, RustRover, Rider, Android Studio |
| `godot/` | Godot 4 |
| `unity/` | Unity Editor |
| `claude-code/` | Claude Code hooks |
| `codex/` | Codex / Aider / CLI wrap |
| `openclaw/` | OpenClaw session tail |

Shell hooks: `daemon/hooks.sh`, `daemon/hooks.ps1`.

Both GitHub and Slack are optional, self-hosted, and degrade gracefully:
configure nothing and the tracker still runs.
