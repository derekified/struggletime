# The daemon

One python file (`daemon/daemon.py`), stdlib only, polling instead of
native watchers so it runs the same on a 10-year-old windows laptop as
on a new mac. expect tens of mb of ram and near-zero cpu while idle.

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

every 5 minutes (`AITIME_BUCKET_SECS` to change) it seals a bucket:
counts + git anchor + `prev_hash`, hashes it, appends to the local
ledger, and posts the pending batch if a server is configured.

## running it

the gui is the normal way. stdlib tkinter, no installs:

```bash
make gui                 # or: python3 daemon/gui.py
```

pick your handle, repo and server, press **start tracking**. it runs
the same daemon underneath, shows its log live and how many sealed
buckets are waiting to send, and remembers your settings in
`~/.aitime/gui.json`. `inspect what would be sent` runs the inspect
command from inside the window.

headless or scripting, the cli is still there:

```bash
python3 daemon/daemon.py --repo ~/my-project \
  --server http://127.0.0.1:18765 --user you
```

flags: `--project` (default: repo folder name), `--bucket-secs`,
`--once` (seal one bucket and exit, used by tests and demos).

if the server is down, buckets pile up in the ledger and sync later.
if you kill the daemon mid-bucket, that partial bucket is lost and
nothing else is. the hash chain makes both facts visible in review.

## adapters (optional enrichment)

the daemon counts everything above with zero plugins. adapters only
sharpen the signal:

| adapter | what it adds | setup |
|---|---|---|
| vscode / cursor (`adapters/vscode`) | human vs programmatic edits, window focus | load `extension.js` as an extension |
| claude code (`adapters/claude-code`) | exact agent turn boundaries | merge `hooks.json` into `.claude/settings.json` |
| codex / any cli (`adapters/codex`) | turn boundaries for cli agents | shell wrapper in its readme |
| openclaw (`adapters/openclaw`) | turns + tool results from local session logs, counts only | `python3 adapters/openclaw/adapter.py` |

if an adapter breaks when its tool updates, layer 1 keeps counting.
that is the whole point of the split.
