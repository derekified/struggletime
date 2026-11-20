# The daemon

One python file (`daemon/daemon.py`), stdlib only, polling instead of
native watchers so it runs the same on a 10-year-old windows laptop as
on a new mac. expect tens of mb of ram and near-zero cpu while idle.

Settings live in `~/.config/struggletime/settings.json` (XDG; env
overrides via `STRUGGLETIME_CONFIG`). Runtime logs/events stay in
`~/.aitime/`. Legacy `~/.aitime/gui.json` is migrated automatically.
There is no `~/.wakatime.cfg` in this architecture.

## what it watches

- **files** in the repo (`.git` and common build dirs skipped): how many
  changed, in what burst shape. 1–2 files at a time reads as a human
  editing; 5+ files or a huge instant diff reads as an agent/formatter.
- **your shell**, via the hook: command *name* only, classified locally
  (`git`, `test_runner`, `compiler`, `ai_cli`, `other`), duration and
  exit code. arguments are never recorded.
- **git**: head commit, dirty file count, added/deleted line totals,
  per-file deltas keyed by an anonymized id (`sha256(salt:path)`, salt
  created locally and stored only in the ledger).

every 5 minutes (`AITIME_BUCKET_SECS` / `--bucket-secs` to change) it
seals a bucket: counts + git anchor + `prev_hash`, hashes it, appends
to the local ledger, and posts the pending batch if a server is
configured.

## running it

the menu bar / system tray daemon is the normal way:

```bash
pip install -r daemon/tray-requirements.txt   # one time, two small packages
make tray                                     # or: python3 daemon/tray.py
```

it lives in the menu bar on macos, the system tray on windows, and
the appindicator area on linux. it starts tracking on launch if you
are linked via API key and set up, shows today's weighted time next to
the icon (`42m`, `1h 15m`), refreshed every 15 seconds, and its menu
has start/stop, **set / link API key** (paste the key from dashboard
Settings → StruggleTime API key into `~/.config/struggletime/settings.json`), clear key,
**link / create project** (project list, optional GitHub repo with None,
local directory picker → API + `gui.json`), open dashboard, inspect,
settings, and quit.
linux needs a tray backend
(`sudo apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1`);
gnome without the appindicator extension has no tray area — the tray
falls back to a headless CLI loop, or use the gui.

settings use **native OS dialogs** (no tkinter): osascript on macOS,
zenity/kdialog on Linux, PowerShell forms on Windows, with a terminal
menu fallback when headless.

```bash
make gui                 # or: python3 daemon/gui.py
```

**set API key** (from Settings on the dashboard), then **link / create
project** (project + optional GitHub / None + local directory), start
tracking. settings are stored in `~/.config/struggletime/settings.json`,
which the tray and Layer-1 daemon read too. `inspect what would be sent`
shows a native alert with the inspect output.

start on login (tray + linked daemon):

```bash
sh daemon/autostart.sh    # or: sh daemon/install.sh
```

this writes `~/.config/autostart/struggletime.desktop` (Linux), a systemd
user unit, or a macOS LaunchAgent, and installs `~/.local/bin/struggletime-login`.

headless or scripting, the cli is still there:

```bash
python3 daemon/daemon.py --repo ~/my-project \
  --server http://62.83.116.143:18765 --user you
```

or via the helper (background + logs):

```bash
python3 daemon/helper.py start --repo ~/my-project --user you \
  --server http://62.83.116.143:18765 --token "$ST_API_KEY"
python3 daemon/helper.py status
python3 daemon/helper.py sync --repo ~/my-project --user you   # flush pending now
python3 daemon/helper.py pause
python3 daemon/helper.py resume
python3 daemon/helper.py stop
python3 daemon/tray.py --headless
```

flags: `--project` (default: repo folder name), `--bucket-secs`,
`--token` (session bearer, also `STRUGGLETIME_TOKEN` / `AITIME_TOKEN`),
`--once` (seal one bucket and exit, used by tests and demos),
`--flush-only` (retry pending ledger upload and exit).

browser opens from the tray/gui go through `daemon/browser_auth.py`
(`open` / `xdg-open` / `os.startfile`, with a sanitized env on linux) so
the **system default browser** is used even when the tray process has a
weird `LD_LIBRARY_PATH`.

## stress / acceptance (internal)

```bash
make stress-smoke          # 5 projects, ephemeral server, cleanup
make stress                # 1000 weighted projects + buckets + report
python3 daemon/stress_test.py --count 1000 --cleanup-after
```

creates taggable projects `sts{run_id}{nnnn}`, posts sealed buckets,
verifies listing/stats, prints p50/p95 latency, and can `--cleanup`
soft-delete them afterward.

on start the daemon retries any pending ledger buckets with backoff.
if the server is down, buckets pile up in the ledger and sync later.
SIGTERM/SIGINT flush pending then exit. if you kill the daemon
mid-bucket, that partial bucket is lost and nothing else is. the hash
chain makes both facts visible in review.

## editor / agent attribution (extensible, no plugins required)

Layer 1 maps running processes to `editor` / `agent` via
`daemon/editors.py`. Built-ins cover Cursor, VS Code, Windsurf, Trae,
Kiro, Zed, VSCodium, Neovim, Helix, Claude Code, Codex, OpenClaw, Aider,
Goose, OpenCode, Gemini CLI, and more. Override or extend without code
changes:

```jsonc
// ~/.config/struggletime/editors.json
{
  "signatures": [
    {"match": "my-ide", "editor": "my-ide", "kind": "ai_ide"},
    {"match": "my-cli", "editor": "my-cli", "agent": "my-cli", "kind": "cli_agent"}
  ],
  "aliases": { "code": "vscode" }
}
```

`kind` is `ai_ide` | `editor` | `cli_agent`. Unknown tools that still
touch the linked repo get a sanitized process-name label (never
`local-daemon`) combined with burst shape (1–2 human vs 5+ agent).

## adapters (optional enrichment — no MCP)

adapters emit compact allowlisted lines into `~/.aitime/agent_events.jsonl`
(or via `python3 daemon/agent_event.py …`). the daemon drains that file on
each poll tick, then falls back to the registry above so
`os` / `language` / `editor` / `agent` stay populated. there is **no MCP
server** and no raw paths, prompts, or keystrokes in the bridge.

| adapter | what it adds | setup |
|---|---|---|
| vscode / cursor (`adapters/vscode`) | human edits, focus, file op counts | load `extension.js` as an extension |
| claude code (`adapters/claude-code`) | agent turn boundaries | merge `hooks.json` into `.claude/settings.json` |
| codex / any cli (`adapters/codex`) | turn boundaries for cli agents | shell wrapper / `agent_event.py` in its readme |
| openclaw (`adapters/openclaw`) | turns + tool result counts from local session logs | `python3 adapters/openclaw/adapter.py` |

legacy `~/.aitime/exec.jsonl` is still drained for older hooks. if an
adapter breaks when its tool updates, layer 1 keeps counting — that is
the whole point of the split.
