# StruggleTime Tray

The **StruggleTime Tray** is the normal way to run tracking on a developer
machine. It is the GUI twin of `struggletime-helper`: same settings file,
same daemon process, same pause/seal behavior.

## What the tray does

- Starts and stops `daemon/daemon.py` in the background
- Shows today's weighted hours next to the icon (refreshed about every 15 seconds)
- Lets you paste a StruggleTime API key (`st_…`) from the dashboard
- Links a local directory to a dashboard project
- Opens the web dashboard and settings path
- Mirrors CLI actions: start, stop, pause, resume, sync/flush

```bash
pip install -r daemon/tray-requirements.txt
make tray
```

## Pause vs seal

| Action | Effect |
|---|---|
| Active | Watcher runs and seals 5-minute buckets into the local ledger |
| Pause | Sealing is suspended until you resume |
| Stop | Daemon process exits; pending ledger files remain |
| Sync | Posts pending sealed buckets to the server |

## Privacy

The tray only orchestrates the stdlib daemon. Ledgers store allowlisted
metadata, salted file IDs (`sha256(salt:path)`), and `prev_hash` links —
never prompts, transcripts, file contents, or unhashed paths.

See also: [daemon.md](daemon.md), Fumadocs page `/docs/tray/`.
