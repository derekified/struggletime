# Daemon assets

Installers, shell hooks, and tray assets for StruggleTime.

The tracker itself is the unified Rust binary — both desktop tray app and CLI:

```bash
# install App + CLI (macOS / Linux)
sh install.sh
# Windows
powershell -ExecutionPolicy Bypass -File install.ps1

struggletime daemon --repo ~/my-project
struggletime start --repo ~/my-project
struggletime --tray
struggletime status
```

Build with `make rust` or download from
[Releases](https://github.com/derekified/struggletime/releases).

Hooks (`hooks.sh` / `hooks.ps1`) emit allowlisted exec categories only.
Never argv or environment secrets.
